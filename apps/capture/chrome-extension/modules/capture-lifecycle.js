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
import { apexForHost, takesSubdomains } from './capture-scope.js';

export const STATE_KEY = 'captureState';
const BOOT_KEY = 'captureBootMarker';
const DRAIN_DEADLINE_MS = 10 * 60 * 1000;
const DRAIN_POLL_MS = 3000;
const SERVER_IDLE_END_MS = 24 * 60 * 60 * 1000;  // the platform auto-ends an idle capture

export class CaptureLifecycle {
  constructor({ local, session, api, captureSession, uploader, applyGate, getObservations, now, sleep }) {
    this.local = local;                    // chrome.storage.local-shaped
    this.sessionStorage = session;         // chrome.storage.session-shaped (boot marker)
    this.api = api;                        // CaptureApi
    this.captureSession = captureSession;  // CaptureSession (id, binding, dedup)
    this.uploader = uploader;              // BatchUploader
    this.applyGate = applyGate;            // ({ scope, capturing }) => Promise — the capture gate
    this.getObservations = getObservations || (() => []);
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
      if (this.state && !this.state.paused && !this.state.ending) {
        this.foldElapsed();
        this.state.paused = true;  // browser restart: resume paused, scope intact
        await this.persist();
      }
    }
    if (this.state) await this.applyGate({ scope: this.state.scope, capturing: this.isListening() });
    if (this.state && this.state.ending) this.drainAndEnd();  // background; survives the popup
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
      const entry = String(scope || '').trim().toLowerCase();
      if (!entry) return { ok: false, error: 'no_scope' };
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
        elapsedMs: 0, paused: false, ending: null, newSites: [], lastBatchAt: this.now(),
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
      // The platform auto-ends a capture with no new files for a day; don't revive a dead one.
      if (this.now() - (s.lastBatchAt || s.startedAt) > SERVER_IDLE_END_MS) {
        await this.closeLocally('idle');
        return { ok: false, error: 'capture_ended' };
      }
      s.paused = false;
      s.resumedAt = this.now();
      await this.persist();
      await this.applyGate({ scope: s.scope, capturing: true });
      return { ok: true };
    });
  }

  noteBatch() {
    if (this.state) this.state.lastBatchAt = this.now();  // persisted with the next transition
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
        if (!(st.pendingQueueLength > 0) && !st.isUploading) break;
        if (this.now() > deadline) break;  // the server's sweeper will still end it
        await this.sleep(DRAIN_POLL_MS);
      }
      const s = this.state;
      if (!s || !s.ending) return;
      const res = await this.api.end(s.sessionId, { runId: s.runId, observations: this.getObservations() });
      if (res.ok || (res.status && res.status !== 401 && res.status < 500)) {
        await this.serialize(() => this.closeLocally('ended'));
      }
    })().finally(() => { this.draining = null; });
    return this.draining;
  }

  resumeAfterLogin() {
    if (this.state && this.state.ending) this.drainAndEnd();
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
      this.state = null;
      this.lastEnded = null;
      await this.persist();
      await this.applyGate({ scope: [], capturing: false });
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
