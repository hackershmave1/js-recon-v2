// capture-lifecycle.js — the worker-owned state of ONE capture (Start → End), the extension half of
// the platform's capture lifecycle (spec: apps/platform/docs/superpowers/specs/
// 2026-11-07-capture-lifecycle-design.md, "Extension (PR 3a worker, PR 3b popup)").
//
// The worker owns it; the popup only reads `snapshot()` (via getStatus) and asks for transitions.
// Everything is persisted in storage.local BEFORE it matters (MV3 kills the worker after ~30 s idle),
// and every transition runs on one promise chain, so a quick End → Start can't interleave.
//
//   idle ─start─▶ capturing ⇄ paused ─end─▶ ending (durable; resumed after a respawn) ─▶ idle
//
// - Restart vs cold start: storage.session survives worker cold starts but not a browser restart,
//   so a missing boot marker with a capture in progress means the browser restarted → paused.
// - End replies at once (the popup's 60 s safety net) and drains in the background. A pending End
//   is resumed by `recover()` (init) and `resumeAfterLogin()`.
// - A capture the server ended (409 capture_ended on its batches) closes here via `endedRemotely`.
import { apexForHost, takesSubdomains, isNoiseHost, inScope } from './capture-scope.js';
import { normalizeRootDomains } from './normalize-scope.js';

export const STATE_KEY = 'captureState';
const BOOT_KEY = 'captureBootMarker';
const DRAIN_DEADLINE_MS = 10 * 60 * 1000;
const DRAIN_POLL_MS = 3000;
const ACTIVITY_PERSIST_MS = 60 * 1000;  // lastBatchAt is written at most this often
const MAX_NEW_SITES = 20;
const DOT = '•';

// The toolbar badge: the capture's file count, plus a dot while a "new site seen" is unanswered.
// The dot survives the count (one writer: updateBadge) and stays while paused, so it's noticed with
// the popup closed. Chrome fits about four characters.
export function badgeText({ capturing, files, newSites }) {
  const dot = newSites > 0 ? DOT : '';
  if (!capturing) return dot;
  if (dot) return (files > 99 ? '99+' : String(files)) + dot;
  return files > 999 ? '999+' : String(files);
}

export class CaptureLifecycle {
  constructor({
    local, session, api, captureSession, uploader, applyGate, getObservations, getProcessing,
    scheduleRetry, now, sleep,
  }) {
    this.local = local;                    // chrome.storage.local-shaped
    this.sessionStorage = session;         // chrome.storage.session-shaped (boot marker)
    this.api = api;                        // CaptureApi
    this.captureSession = captureSession;  // CaptureSession (id, binding, dedup)
    this.uploader = uploader;              // BatchUploader
    this.applyGate = applyGate;            // ({ scope, capturing }) => Promise — the capture gate
    this.getObservations = getObservations || (() => []);
    // Captured requests not yet turned into upload-queue files (queued + in processFile).
    this.getProcessing = getProcessing || (() => 0);
    // Arms a later retry of a pending End (a chrome.alarm): the worker may stay alive for hours.
    this.scheduleRetry = scheduleRetry || (() => {});
    this.now = now || (() => Date.now());
    this.sleep = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
    this.state = null;                     // the current capture, or null
    this.lastEnded = null;                 // { runId, scope, at, reason } for the "Capture ended" banner
    this.chain = Promise.resolve();
  }

  // One transition at a time, in order. A failure answers its caller and never wedges the chain.
  serialize(fn) {
    const run = this.chain.then(fn, fn);
    this.chain = run.catch(() => {});
    return run;
  }

  async persist() {
    await this.local.set({ [STATE_KEY]: { capture: this.state, lastEnded: this.lastEnded } });
  }

  snapshot() {
    const s = this.state;
    return {
      capture: s && {
        runId: s.runId, sessionId: s.sessionId, scope: s.scope, paused: s.paused, ending: !!s.ending,
        elapsedMs: s.elapsedMs + (s.paused || s.ending ? 0 : this.now() - s.resumedAt),
        newSites: s.newSites,
        // For the popup's header only: how the capture began and what paused it.
        mode: s.mode || null, ordinal: s.ordinal || null, pausedByRestart: !!s.pausedByRestart,
      },
      lastEnded: this.lastEnded,
    };
  }

  // The runId a newly captured file is stamped with, or null (not capturing: the legacy path).
  runIdForEnqueue() {
    const s = this.state;
    return s && !s.paused && !s.ending ? s.runId : null;
  }

  // Worker start. Restores the capture, pauses it after a browser restart, resumes a pending End.
  async recover() {
    const stored = (await this.local.get(STATE_KEY))[STATE_KEY] || {};
    this.state = stored.capture || null;
    this.lastEnded = stored.lastEnded || null;
    const booted = (await this.sessionStorage.get(BOOT_KEY))[BOOT_KEY];
    if (!booted) {
      await this.sessionStorage.set({ [BOOT_KEY]: this.now() });
      const s = this.state;
      if (s && !s.paused && !s.ending) {
        // Browser restart: resume paused, scope intact. Count time only up to the last captured
        // file, not the hours the browser was closed.
        s.elapsedMs += Math.max(0, Math.max(s.resumedAt, s.lastBatchAt || 0) - s.resumedAt);
        s.paused = true;
        s.pausedByRestart = true;
        await this.persist();
      }
    }
    if (this.state) await this.applyGate({ scope: this.state.scope, capturing: this.isListening() });
  }

  // Finish a pending End. Called once the outbox is loaded (end of initialize), after login, and
  // by the retry alarm — never before the outbox is rehydrated, or End would post capture/end with
  // this capture's unsent files still on disk (they'd then be refused).
  resumePendingEnd() {
    if (this.state && this.state.ending) this.drainAndEnd();
  }

  isListening() {
    return !!this.state && !this.state.paused && !this.state.ending;
  }

  foldElapsed() {
    const s = this.state;
    if (s && !s.paused && !s.ending) s.elapsedMs += this.now() - s.resumedAt;
  }

  // Scope for a tab host: the registrable domain (+ all subdomains), exact for IPs/localhost.
  static scopeForHost(host) {
    const scope = apexForHost(host);
    return { scope, subdomains: takesSubdomains(scope) };
  }

  lookup(scope) {
    return this.api.lookup(scope);
  }

  // Start a capture: mode "new" (a fresh session, bound to `binding`) or "continue" (adopt the
  // matched session's id AND its engagement — the platform binds a session to one, immutably).
  start({ scope, mode, match, binding }) {
    return this.serialize(async () => {
      if (this.state) return { ok: false, error: this.state.ending ? 'ending' : 'capture_in_progress' };
      // Normalized like every other scope write (D40): a URL or `*.acme.io` must not become a
      // gate entry that matches no host.
      const entry = normalizeRootDomains([scope])[0];
      if (!entry) return { ok: false, error: 'no_scope' };
      // NOTE: the session switch below is not rolled back if the platform then refuses the start:
      // the next Start simply rotates/adopts again. The engagement's capture config is not
      // re-applied here (Start is about scope and session, not per-engagement capture settings).
      if (mode === 'continue') {
        if (!match || !match.external_id) return { ok: false, error: 'no_match' };
        await this.captureSession.adopt(match.external_id, {
          binding: { projectId: match.engagement_id || null, captureConfig: {}, overrideKeys: [] },
        });
      } else {
        await this.captureSession.startNew({ binding: binding || null });
      }
      const sessionId = this.captureSession.sessionId;
      const res = await this.api.start(sessionId, {
        rootDomains: [entry],
        projectId: mode === 'continue' ? match.engagement_id : binding && binding.projectId,
      });
      if (!res.ok || !res.data || !res.data.runId) {
        return { ok: false, error: res.status === 401 ? 'session_expired' : (res.error || 'start_failed') };
      }
      this.captureSession.resetForCapture();  // dedup per capture: a Continue re-sends its files
      this.state = {
        runId: res.data.runId, sessionId, scope: [entry], startedAt: this.now(), resumedAt: this.now(),
        elapsedMs: 0, paused: false, ending: null, newSites: [], dismissedSites: [], lastBatchAt: this.now(),
        // Shown in the popup header ("Continuing acme.io · capture 5"). The number counts the
        // session's captures at the lookup, so reusing an empty open capture can read one high.
        mode: mode === 'continue' ? 'continue' : 'new',
        ordinal: mode === 'continue' ? (Number(match.captures) || 0) + 1 : 1,
      };
      this.lastEnded = null;
      await this.persist();
      await this.applyGate({ scope: this.state.scope, capturing: true });
      return { ok: true, runId: this.state.runId, sessionId };
    });
  }

  pause() {
    return this.serialize(async () => {
      if (!this.isListening()) return { ok: !!this.state };
      this.foldElapsed();
      this.state.paused = true;
      await this.persist();
      await this.applyGate({ scope: this.state.scope, capturing: false });
      return { ok: true };
    });
  }

  resume() {
    return this.serialize(async () => {
      const s = this.state;
      if (!s || s.ending) return { ok: false, error: 'no_capture' };
      if (!s.paused) return { ok: true };
      // No local idle check: the platform measures idle from its last STORED file and ends the
      // capture itself; if it has, the next batch's 409 capture_ended closes it here.
      s.paused = false;
      s.pausedByRestart = false;
      s.resumedAt = this.now();
      await this.persist();
      await this.applyGate({ scope: s.scope, capturing: true });
      return { ok: true };
    });
  }

  // A file was stamped for this capture. Persisted at most once a minute (MV3 kills the worker after
  // ~30 s idle, and a browser restart's elapsed time is bounded by it).
  noteBatch() {
    const s = this.state;
    if (!s) return;
    const before = s.lastBatchAt || 0;
    s.lastBatchAt = this.now();
    if (s.lastBatchAt - before >= ACTIVITY_PERSIST_MS) this.persist().catch(() => {});
  }

  // End: close the gate and answer NOW; uploads drain and the platform is told in the background.
  end() {
    return this.serialize(async () => {
      const s = this.state;
      if (!s) return { ok: true, ended: true };
      if (!s.ending) {
        this.foldElapsed();
        s.ending = s.runId;
        await this.persist();
        await this.applyGate({ scope: s.scope, capturing: false });
      }
      this.drainAndEnd();
      return { ok: true, ending: true };
    });
  }

  // Wait for this capture's queued files (bounded), then POST capture/end. Leaves `ending` set on a
  // failure the next init/login retries (expired sign-in, network). Single-flight.
  async drainAndEnd() {
    if (this.draining) return this.draining;
    this.draining = (async () => {
      const deadline = this.now() + DRAIN_DEADLINE_MS;
      for (;;) {
        await this.uploader.flushAll();
        const st = this.uploader.getStats();
        if (st.authPaused) return;  // resumeAfterLogin() picks it up
        // Done only when nothing is left to become a file (captured requests still being
        // processed) AND nothing is left to send.
        if (this.getProcessing() === 0 && !(st.pendingQueueLength > 0) && !st.isUploading) break;
        if (this.now() > deadline) { this.scheduleRetry(); return; }  // never end on a partial set
        await this.sleep(DRAIN_POLL_MS);
      }
      const s = this.state;
      if (!s || !s.ending) return;
      const res = await this.api.end(s.sessionId, { runId: s.runId, observations: this.getObservations() });
      if (res.ok || (res.status && res.status !== 401 && res.status < 500)) {
        await this.serialize(() => this.closeLocally('ended'));
      } else if (res.status !== 401) {
        this.scheduleRetry();  // network / 5xx: try again (a 401 waits for login)
      }
    })().finally(() => { this.draining = null; });
    return this.draining;
  }

  resumeAfterLogin() {
    this.resumePendingEnd();
  }

  // The server ended this capture (409 capture_ended on its batches): close it here too.
  endedRemotely(runId) {
    return this.serialize(async () => {
      if (this.state && this.state.runId === runId) await this.closeLocally('ended_elsewhere');
    });
  }

  // Logout / tenant change: forget the capture locally (the platform's 24 h sweeper ends it).
  forget() {
    return this.serialize(async () => {
      const had = !!this.state;
      this.state = null;
      this.lastEnded = null;
      await this.persist();
      if (had) await this.applyGate({ scope: [], capturing: false });
    });
  }

  async closeLocally(reason) {
    const s = this.state;
    if (!s) return;
    this.lastEnded = { runId: s.runId, sessionId: s.sessionId, scope: s.scope, at: this.now(), reason };
    this.state = null;
    await this.persist();
    await this.applyGate({ scope: [], capturing: false });
  }

  // A script loaded from outside the scope (background.noteOutOfScopeScript). Noted once per site
  // (registrable domain) for the popup's "New site seen" badge, unless it's a known tracker/CDN, the
  // capture isn't listening, or "capture every tab" is on (everything is in). Returns whether a new
  // site was added (the caller refreshes the toolbar badge).
  noteSite(host, { captureEverything = false } = {}) {
    const s = this.state;
    if (!this.isListening() || captureEverything || !host) return false;
    if (isNoiseHost(host) || inScope(host, s.scope)) return false;
    const site = apexForHost(host);
    if (!site || s.newSites.includes(site) || (s.dismissedSites || []).includes(site)) return false;
    if (s.newSites.length >= MAX_NEW_SITES) return false;
    s.newSites.push(site);
    this.persist().catch(() => {});
    return true;
  }

  // "Done" on the popup's "Capture ended" banner.
  dismissEnded() {
    return this.serialize(async () => {
      this.lastEnded = null;
      await this.persist();
      return { ok: true };
    });
  }

  dismissSite(site) {
    return this.serialize(async () => {
      if (!this.state) return { ok: false, error: 'no_capture' };
      this.state.newSites = this.state.newSites.filter((h) => h !== site);
      this.state.dismissedSites = [...new Set([...(this.state.dismissedSites || []), site])];
      await this.persist();
      return { ok: true };
    });
  }

  addToScope(host) {
    return this.serialize(async () => {
      const s = this.state;
      if (!s) return { ok: false, error: 'no_capture' };
      const { scope } = CaptureLifecycle.scopeForHost(host);
      const res = await this.api.addScope(s.sessionId, scope);
      if (!res.ok) return { ok: false, error: res.status === 401 ? 'session_expired' : (res.error || 'failed') };
      if (!s.scope.includes(scope)) s.scope.push(scope);
      s.newSites = s.newSites.filter((h) => h !== scope);
      await this.persist();
      await this.applyGate({ scope: s.scope, capturing: this.isListening() });
      return { ok: true, scope: s.scope };
    });
  }
}
