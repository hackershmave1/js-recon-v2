// workspace-client.js — talks to the workspace backend for the non-ingestion calls:
// health check, on-demand analysis start, and analysis progress. Extracted from
// background.js. Each method RETURNS a plain result object (the caller does the
// chrome sendResponse); the client never touches the messaging channel.

export class WorkspaceClient {
  // Dependencies are injected so the client doesn't reach back into JSExtractor:
  //   getSettings()  -> the live settings object (for workspaceUrl)
  //   getSessionId() -> the current session id (for the analyze endpoints)
  //   batchUploader  -> used only for the bounded pre-analyze flush
  constructor({ getSettings, getSessionId, batchUploader } = {}) {
    this.getSettings = typeof getSettings === 'function' ? getSettings : () => ({});
    this.getSessionId = typeof getSessionId === 'function' ? getSessionId : () => '';
    this.batchUploader = batchUploader || null;
  }

  // Workspace API origin (no trailing slash) for non-ingestion calls (health, analyze).
  // Single source of truth: the user-entered Workspace URL (default localhost:3000).
  resolveApiBase() {
    const s = this.getSettings() || {};
    let base = (s.workspaceUrl || '').trim() || 'http://localhost:3000';
    // A scheme-less workspace URL (e.g. "localhost:3000") would resolve relative to the
    // extension origin and fail — prepend http:// so it's an absolute URL.
    if (base && !/^[a-z][a-z0-9+.-]*:\/\//i.test(base)) base = 'http://' + base;
    return base.replace(/\/+$/, '');
  }

  async testConnection() {
    const target = this.resolveApiBase() + '/api/health';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    const startedAt = Date.now();
    try {
      const resp = await fetch(target, { method: 'GET', signal: controller.signal });
      clearTimeout(timer);
      return { ok: resp.ok, status: resp.status, latencyMs: Date.now() - startedAt };
    } catch (error) {
      clearTimeout(timer);
      return { ok: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
  }

  // Trigger on-demand analysis of the current session (the decoupled path): flush any
  // unsent captures first, then kick the backend's async threaded job so the single
  // worker isn't blocked inline during capture.
  async analyzeSession() {
    // Best-effort flush so the server has as many captures as possible before analyzing,
    // but BOUNDED — a slow/unreachable workspace must not stall the analyze trigger (the
    // outbox timer/alarm keeps draining in the background regardless).
    if (this.batchUploader) {
      try {
        await Promise.race([
          this.batchUploader.flushAll(),
          new Promise((resolve) => setTimeout(resolve, 8000))
        ]);
      } catch (e) {
        // A flush failure shouldn't block analyzing what did upload.
        console.warn('Flush before analyze failed:', e);
      }
    }
    const target = `${this.resolveApiBase()}/api/sessions/${encodeURIComponent(this.getSessionId())}/analyze/start`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const resp = await fetch(target, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ options: {} }),
        signal: controller.signal
      });
      clearTimeout(timer);
      if (!resp.ok) {
        return { success: false, status: resp.status, error: `HTTP ${resp.status}` };
      }
      const data = await resp.json();
      return { success: true, started: data.started !== false, message: data.message, job: data.job };
    } catch (error) {
      clearTimeout(timer);
      return { success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
  }

  // Poll analysis progress for the current session (drives the popup's per-file feed).
  async getAnalysisProgress() {
    const target = `${this.resolveApiBase()}/api/sessions/${encodeURIComponent(this.getSessionId())}/analyze/progress`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const resp = await fetch(target, { method: 'GET', signal: controller.signal });
      clearTimeout(timer);
      if (!resp.ok) {
        return { success: false, status: resp.status };
      }
      const data = await resp.json();
      return { success: true, job: data.job || null };
    } catch (error) {
      clearTimeout(timer);
      return { success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
  }
}
