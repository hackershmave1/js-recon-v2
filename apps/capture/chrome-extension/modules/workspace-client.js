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
  // Single source of truth: the user-entered Workspace URL (default localhost:8000).
  resolveApiBase() {
    const s = this.getSettings() || {};
    let base = (s.workspaceUrl || '').trim() || 'http://localhost:8000';
    // A scheme-less workspace URL (e.g. "localhost:3000") would resolve relative to the
    // extension origin and fail — prepend http:// so it's an absolute URL.
    if (base && !/^[a-z][a-z0-9+.-]*:\/\//i.test(base)) base = 'http://' + base;
    return base.replace(/\/+$/, '');
  }

  // Authorization header for the login session token, if one is configured. Read live
  // from settings (same source as workspaceUrl). Normalize by keeping only printable ASCII
  // (0x21–0x7e) — the same throw-safe normalization as the uploader's setAuthToken (a
  // newline/control char in a header value makes fetch throw); keep the two IDENTICAL.
  // Returned as a spreadable object so a missing token adds nothing (=> the backend's
  // shared-tenant fallback). Deliberately NOT applied to testConnection: /api/health is
  // tenant-agnostic, so it needs no token.
  authHeaders() {
    const s = this.getSettings() || {};
    // The login session token rides as Bearer (the backend verifies it). Keep only printable
    // ASCII (0x21–0x7e); a newline/control char makes fetch throw (identical to setAuthToken).
    // A missing token adds nothing => the backend's shared-tenant fallback (or a 401 when
    // anon capture is disabled).
    const token = String(s.authToken || '').replace(/[^!-~]+/g, '');
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  // Authenticate to the workspace (POST /auth/login) and return the session token + identity.
  // Pre-tenant / pre-auth: no Bearer, no tenant header. The caller stores the returned token so
  // uploads + analyze route to this user's tenant. 401 => bad credentials; 503 => auth disabled.
  async login(username, password) {
    const target = this.resolveApiBase() + '/auth/login';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const resp = await fetch(target, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username || '', password: password || '' }),
        signal: controller.signal
      });
      clearTimeout(timer);
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const b = await resp.json(); if (b && b.detail) detail = b.detail; } catch (e) { /* keep default */ }
        return { success: false, status: resp.status, error: detail };
      }
      const data = await resp.json();
      return { success: true, token: data.token, user: data.user, role: data.role, tenant: data.tenant || null };
    } catch (error) {
      clearTimeout(timer);
      return { success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
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
      // Don't analyze on a PARTIAL set (DEBT D43c): if captures are still queued, OR a batch is
      // mid-flight (pendingQueueLength drops to 0 the instant a batch is spliced out for upload,
      // before it lands — so isUploading must be checked too), analyzing now would run on fewer
      // files than were captured yet report "complete ✓". Block and tell the caller why, so a
      // genuinely-expired session (which will never drain without re-auth) reads differently from
      // uploads that are merely slow.
      const st = this.batchUploader.getStats();
      if ((st.pendingQueueLength || 0) > 0 || st.isUploading) {
        return {
          success: false,
          reason: st.authPaused ? 'session_expired' : 'pending_uploads',
          pending: st.pendingQueueLength || 0
        };
      }
    }
    const target = `${this.resolveApiBase()}/api/sessions/${encodeURIComponent(this.getSessionId())}/analyze/start`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const resp = await fetch(target, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
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
      const resp = await fetch(target, { method: 'GET', headers: { ...this.authHeaders() }, signal: controller.signal });
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

  // List engagements (projects) from the workspace. Mirrors the inline fetch+timeout template.
  async listProjects() {
    const target = this.resolveApiBase() + '/api/projects';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const resp = await fetch(target, { method: 'GET', headers: { ...this.authHeaders() }, signal: controller.signal });
      clearTimeout(timer);
      if (!resp.ok) return { success: false, status: resp.status, error: `HTTP ${resp.status}` };
      const projects = await resp.json();
      return { success: true, projects: Array.isArray(projects) ? projects : [] };
    } catch (error) {
      clearTimeout(timer);
      return { success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
  }

  // Create a project (quick-create from the popup: name + scope; backend fills the rest).
  async createProject(project) {
    const target = this.resolveApiBase() + '/api/projects';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const resp = await fetch(target, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
        body: JSON.stringify(project || {}),
        signal: controller.signal
      });
      clearTimeout(timer);
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const body = await resp.json(); if (body && body.detail) detail = body.detail; } catch (e) { /* keep default */ }
        return { success: false, status: resp.status, error: detail };
      }
      return { success: true, project: await resp.json() };
    } catch (error) {
      clearTimeout(timer);
      return { success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    }
  }
}
