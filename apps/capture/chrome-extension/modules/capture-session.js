// The capture session: everything that belongs to one capture session id — the captured files,
// dedup set, observations, counters and the engagement binding — plus their persistence and the
// three transitions between sessions. The orderings inside those transitions are load-bearing
// (apps/capture/CLAUDE.md): flush the old id's batches before rotating, drop stragglers before a
// new id exists, persist the new id and its binding in ONE storage write (a respawn never pairs the
// new id with the old engagement, D69), and on a tenant change unbind and drop the outbox so one
// tenant's captures never flush under another's token. Dependencies are injected so the orderings
// are tested at runtime (tests/test_capture_session.mjs, tests/test_session_rotation_races.mjs).
//
// ponytail: state is exposed as public fields because processFile (background.js) still writes
// them directly; narrow this interface when the capture pipeline is deepened.
//
// NOTE: background.js mutates this one instance across rotations; never replace it with a new
// CaptureSession, or an in-flight processFile would write into the orphaned one.

const CAPTURED_META_KEY = 'capturedFilesMeta';
const OBSERVATIONS_KEY = 'capturedObservations';
const BINDING_KEY = 'pendingSessionConfig';
const PERSIST_DEBOUNCE_MS = 750;

export class CaptureSession {
  constructor({ storage, sessionStore, dedupStore, uploader, authTracker, getSettings, observationCap }) {
    this.storage = storage;
    this.sessionStore = sessionStore;
    this.dedupStore = dedupStore;
    this.uploader = uploader;
    this.authTracker = authTracker;
    this.getSettings = getSettings;
    this.observationCap = observationCap;
    // Placeholder until restore() loads the persisted id; listeners wait on `ready` first.
    this.sessionId = sessionStore.generate();
    this.capturedFiles = new Map(); // url -> fileObject (for export/display)
    this.capturedHashes = new Map(); // hash -> {url, capturedAt} (for deduplication)
    this.processingQueue = [];
    // Runtime API-call observations (DEBT D45b1); observationKeys dedups on "METHOD url".
    this.observations = [];
    this.observationKeys = new Set();
    this._captureMetaTimer = null;
    this._obsPersistTimer = null;
    this._resetCounters();
  }

  // Cold start: resume the SAME backend session (and its engagement binding) a previous worker
  // instance was using, with the counters the popup shows.
  async restore() {
    this.sessionId = await this.sessionStore.loadOrCreate();
    const binding = (await this.storage.get(BINDING_KEY))[BINDING_KEY];
    if (binding && typeof binding === 'object') this.uploader.setConfig(binding);
    await this._rehydrateDedup();
    await this._rehydrateCapturedMeta();
    await this._rehydrateObservations();
  }

  // "New session": the platform binds a session to one engagement immutably, so a new binding
  // means a new id.
  async startNew({ binding }) {
    // Drop not-yet-processed captures BEFORE rotating so a queued straggler can't be stamped with
    // the new id; flush already-built batches under the old id first. A batch processQueue already
    // took keeps the id it was taken under and stops at the rotation (takeBatch / isCurrent, D70).
    this.processingQueue = [];
    await this.uploader.flushAll();
    this._switchTo(await this.sessionStore.rotate({ [BINDING_KEY]: binding }), binding);
  }

  // "Continue": take over an EXISTING session (its id and its engagement binding — the platform
  // binds a session to one engagement, immutably) instead of minting a fresh one. Same orderings
  // as startNew: drop stragglers, flush the old id's batches, then switch.
  async adopt(sessionId, { binding } = {}) {
    this.processingQueue = [];
    await this.uploader.flushAll();
    this._switchTo(await this.sessionStore.save(sessionId, { [BINDING_KEY]: binding }), binding);
  }

  // Take up to `size` queued requests to process. A dependency keeps its parent's session id; any
  // other item gets the id current when it is taken. Every transition empties the queue first, so
  // that is the id it was queued under, or (queued while the transition was flushing) the new
  // session it was admitted into (DEBT D70).
  takeBatch(size) {
    const sessionId = this.sessionId;
    return this.processingQueue.splice(0, size).map((item) => (item.sessionId ? item : { ...item, sessionId }));
  }

  // False once a transition replaced `sessionId`: work taken under it must not be filed into the
  // session that replaced it.
  isCurrent(sessionId) {
    return sessionId === this.sessionId;
  }

  // A new capture in the SAME session (Continue, or Start right after a rotation): dedup is per
  // capture, so files seen in an earlier capture are sent again and analyzed in this one.
  resetForCapture() {
    this._clear();
  }

  // Logout / different-tenant login: a fresh, unbound session. History is not written, so one
  // tenant's session never lands in the list another tenant reads.
  async startStandalone({ dropOutbox = false } = {}) {
    this.processingQueue = [];
    // Unbind the live binding AND the persisted snapshot together with the id, or a respawn
    // re-binds the old (possibly other tenant's) engagement. restore() reads null as unbound.
    this._switchTo(await this.sessionStore.rotate({ [BINDING_KEY]: null }), null);
    try {
      await this.storage.remove(BINDING_KEY);
    } catch (e) {
      // best-effort tidy-up; the null written with the id already unbinds it.
    }
    // Tenant change only: the outbox drains under whatever token is CURRENT, so the previous
    // tenant's queued JS would otherwise flush under the new tenant's token. Losing a few unsent
    // files is the safe trade.
    if (dropOutbox) await this.uploader.clearOutbox();
  }

  // A lean, content-free projection of one captured file — just what the popup counter renders. Rehydrated objects carry dependencyCount (not the full
  // dependencies array) to stay small.
  projectCapturedFile(f) {
    return {
      url: f.url,
      contentHash: f.contentHash,
      contentLength: f.contentLength,
      capturedAt: f.capturedAt,
      hasSourceMap: f.hasSourceMap === true,
      secretCount: f.secretCount || 0,
      isMinified: f.isMinified === true,
      classification: f.classification || 'app',
      isThirdParty: f.isThirdParty === true,
      dependencyCount: Array.isArray(f.dependencies) ? f.dependencies.length : (f.dependencyCount || 0),
      sourceMapFetchStatus: f.sourceMapFetchStatus,
      sourceMapFetchError: f.sourceMapFetchError
    };
  }

  // Debounced so a burst of captures collapses into one storage write (avoids O(n^2) writes as
  // the map grows). Best-effort: a serialize/quota failure just means the counter resets on the
  // next respawn — it never throws into capture.
  schedulePersistCapturedMeta() {
    if (this._captureMetaTimer) return;
    this._captureMetaTimer = setTimeout(() => {
      this._captureMetaTimer = null;
      const meta = Array.from(this.capturedFiles.values()).map((f) => this.projectCapturedFile(f));
      this.storage.set({ [CAPTURED_META_KEY]: meta }).catch(() => {});
    }, PERSIST_DEBOUNCE_MS);
  }

  // Immediately write the observation list. Persist only { method, url } — never the captured
  // bodies (review Finding 2): keeping them out of chrome.storage avoids writing (redacted) PII.
  persistObservations() {
    const lean = this.observations.map((o) => ({ method: o.method, url: o.url }));
    this.storage.set({ [OBSERVATIONS_KEY]: lean }).catch(() => {});
  }

  // Debounced persist so observations survive an MV3 teardown before the operator clicks Analyze.
  // Best-effort — a miss just loses some endpoint-confirmations, never a capture.
  schedulePersistObservations() {
    if (this._obsPersistTimer) return;
    this._obsPersistTimer = setTimeout(() => {
      this._obsPersistTimer = null;
      this.persistObservations();
    }, PERSIST_DEBOUNCE_MS);
  }

  // The live id and the live binding change together, with no await between them, so nothing
  // running while a transition is suspended sees the new id with the old binding (DEBT D69).
  _switchTo(sessionId, binding) {
    this.sessionId = sessionId;
    this.uploader.setConfig(binding);
    this._clear();
  }

  // Shared by all three transitions: everything captured under the current id goes.
  _clear() {
    this.capturedFiles.clear();
    if (this._captureMetaTimer) { clearTimeout(this._captureMetaTimer); this._captureMetaTimer = null; }
    this.storage.remove(CAPTURED_META_KEY).catch(() => {});
    if (this._obsPersistTimer) { clearTimeout(this._obsPersistTimer); this._obsPersistTimer = null; }
    try { this.storage.remove(OBSERVATIONS_KEY).catch(() => {}); } catch (e) { /* best effort */ }
    this.capturedHashes.clear();
    this.dedupStore.clear().catch(() => {});
    this.authTracker.clear();
    this.observations = [];
    this.observationKeys.clear();
    this._resetCounters();
  }

  _resetCounters() {
    // Script hosts outside the target root (D44 discovery aid), per-origin inline-script counts
    // (D45a flood guard), request bodies awaiting their onCompleted (D45b2) and the byte budgets.
    this.outOfScopeHosts = new Map();
    this.inlinePerPage = new Map();
    this.pendingRequestBodies = new Map();
    this.bodyBytesUsed = 0;
    this.totalCapturedBytes = 0;
    this.processingStats = {
      processedFiles: 0,
      failedFiles: 0,
      lastFailureReason: null,
      lastFailureUrl: null,
      lastFailureMessage: null
    };
  }

  // Rebuild the in-memory dedup set from the persistent store so a respawn doesn't re-fetch or
  // re-upload files already captured in this session.
  async _rehydrateDedup() {
    try {
      const entries = (await this.dedupStore.getAll()) || [];
      for (const entry of entries) {
        if (entry && entry.contentHash) {
          this.capturedHashes.set(entry.contentHash, { url: entry.url, capturedAt: entry.capturedAt });
        }
      }
    } catch (e) {
      console.warn('Dedup rehydrate failed:', e);
    }
  }

  // Only fills urls not already present (a live capture that beat rehydrate wins). Export
  // tolerates these lean objects — missing fields serialize as absent.
  async _rehydrateCapturedMeta() {
    try {
      const stored = (await this.storage.get(CAPTURED_META_KEY))[CAPTURED_META_KEY];
      if (!Array.isArray(stored)) return;
      for (const f of stored) {
        if (f && f.url && !this.capturedFiles.has(f.url)) this.capturedFiles.set(f.url, f);
      }
      // Rebuild totalCapturedBytes so the session byte cap (enforceLimits) holds across respawns.
      let total = 0;
      for (const [, meta] of this.capturedFiles) total += (meta.contentLength || 0);
      this.totalCapturedBytes = total;
    } catch (e) {
      // no persisted meta / storage unavailable — the counter just starts empty
    }
  }

  async _rehydrateObservations() {
    try {
      const stored = (await this.storage.get(OBSERVATIONS_KEY))[OBSERVATIONS_KEY];
      if (!Array.isArray(stored)) return;
      for (const o of stored) {
        if (!o || typeof o.method !== 'string' || typeof o.url !== 'string') continue;
        const key = o.method + ' ' + o.url;
        if (this.observationKeys.has(key)) continue;
        if (this.observationKeys.size >= this.observationCap) break;
        this.observationKeys.add(key);
        this.observations.push({ method: o.method, url: o.url });
      }
    } catch (e) {
      // no persisted observations / storage unavailable — start empty
    }
  }
}
