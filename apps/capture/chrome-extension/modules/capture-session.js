// The capture session: everything that belongs to one capture session id — the captured files,
// dedup set, observations, counters and the engagement binding — plus their persistence and the
// three transitions between sessions. The orderings inside those transitions are load-bearing
// (apps/capture/CLAUDE.md): flush the old id's batches before rotating, drop stragglers before a
// new id exists, and on a tenant change unbind and drop the outbox so one tenant's captures never
// flush under another's token. Dependencies are injected so the orderings are tested at runtime.
//
// ponytail: state is exposed as public fields because processFile (background.js) still writes
// them directly; narrow this interface when the capture pipeline is deepened.
//
// NOTE: background.js mutates this one instance across rotations; never replace it with a new
// CaptureSession, or an in-flight processFile would write into the orphaned one.
import { CAPTURE_HISTORY_KEY, summaryToCache, summaryForSession, addToHistory, historyForTenant } from './capture-history.js';

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
    // Last finished findings summary as { sessionId, summary }, so history only attaches it to
    // the session it describes (D46c).
    this.lastFindingsSummary = null;
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
  // means a new id. `persistWith` lets the caller write its own keys in the SAME storage write as
  // the binding, so a respawn can never see one without the other. `onRotated` runs synchronously
  // right after the new id exists and the old state is cleared — before any await — so the caller
  // can switch its capture gate in the same turn and no event sees the new id with the old gate.
  async startNew({ binding, persistWith = {}, onRotated = () => {} }) {
    // Drop not-yet-processed captures BEFORE rotating so a queued straggler can't be stamped with
    // the new id; flush already-built batches under the old id first. NOTE(DEBT D70): a batch
    // processQueue already spliced out is not covered and can still be stamped with the new id.
    this.processingQueue = [];
    await this.uploader.flushAll();
    await this._saveToHistory();
    this.sessionId = await this.sessionStore.rotate();
    this._clear();
    onRotated();
    await this.storage.set({ ...persistWith, [BINDING_KEY]: binding });
    this.uploader.setConfig(binding);
  }

  // Logout / different-tenant login: a fresh, unbound session. History is not written, so one
  // tenant's session never lands in the list another tenant reads.
  async startStandalone({ dropOutbox = false } = {}) {
    this.processingQueue = [];
    this.sessionId = await this.sessionStore.rotate();
    this._clear();
    // Clear the live binding AND the persisted snapshot, or a respawn re-binds the old engagement.
    this.uploader.setConfig(null);
    try {
      await this.storage.remove(BINDING_KEY);
    } catch (e) {
      // best-effort; the in-memory setConfig(null) already unbound the live uploader.
    }
    // Tenant change only: the outbox drains under whatever token is CURRENT, so the previous
    // tenant's queued JS would otherwise flush under the new tenant's token. Losing a few unsent
    // files is the safe trade.
    if (dropOutbox) await this.uploader.clearOutbox();
  }

  // "Clear files": empty the captured state but keep the session id and binding.
  async clearCaptured() {
    this.processingQueue = [];
    this._clear();
  }

  noteFindingsSummary(sessionId, result) {
    const cached = summaryToCache(sessionId, result);
    if (cached) this.lastFindingsSummary = cached;
  }

  async history(tenantId) {
    try {
      const stored = await this.storage.get(CAPTURE_HISTORY_KEY);
      return historyForTenant(stored[CAPTURE_HISTORY_KEY], tenantId);
    } catch (e) {
      return [];
    }
  }

  // A lean, content-free projection of one captured file — just what the popup counter and the
  // recent-captures feed render. Rehydrated objects carry dependencyCount (not the full
  // dependencies array) to stay small; getFiles reads either shape.
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
    this.lastFindingsSummary = null;
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

  // Persist a snapshot of the session to the capture history before rotating away from it.
  // Skipped when nothing was captured.
  async _saveToHistory() {
    if (!this.capturedFiles.size) return;
    let mapsCount = 0, secretCount = 0;
    for (const f of this.capturedFiles.values()) {
      if (f.hasSourceMap) mapsCount++;
      secretCount += f.secretCount || 0;
    }
    const settings = this.getSettings();
    const entry = {
      sessionId: this.sessionId,
      timestamp: new Date().toISOString(),
      projectId: this.uploader.getStats().projectId || null,
      scope: settings
        ? { rootDomains: settings.domainScopes || [], includeSubdomains: settings.includeSubdomains !== false }
        : null,
      fileCount: this.capturedFiles.size,
      mapsCount,
      secretCount,
      findingsSummary: summaryForSession(this.lastFindingsSummary, this.sessionId),
      workspaceUrl: settings?.workspaceUrl || null,
      tenantId: settings?.authTenantId || '',
    };
    try {
      const stored = await this.storage.get(CAPTURE_HISTORY_KEY);
      await this.storage.set({ [CAPTURE_HISTORY_KEY]: addToHistory(stored[CAPTURE_HISTORY_KEY], entry) });
    } catch (e) { /* storage unavailable — non-fatal */ }
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
