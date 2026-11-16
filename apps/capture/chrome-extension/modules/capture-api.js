// capture-api.js — the platform's capture-lifecycle endpoints (start, end, the Continue lookup,
// Add to scope). Each call returns { ok, status?, data?, error? } and never throws; the caller
// decides. Base URL and auth headers come from the WorkspaceClient (one source of truth).
const TIMEOUT_MS = 15000;

export class CaptureApi {
  constructor({ base, headers, fetchImpl } = {}) {
    this.base = base;        // () => 'http://host:port' (no trailing slash)
    this.headers = headers;  // () => { Authorization?: 'Bearer …' }
    this.fetch = fetchImpl || ((...args) => fetch(...args));
  }

  async request(method, path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const resp = await this.fetch(this.base() + path, {
        method,
        headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...this.headers() },
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      let data = null;
      try { data = await resp.json(); } catch (e) { /* empty or non-JSON body */ }
      if (!resp.ok) return { ok: false, status: resp.status, data, error: `HTTP ${resp.status}` };
      return { ok: true, status: resp.status, data };
    } catch (error) {
      return { ok: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') };
    } finally {
      clearTimeout(timer); // after the body read too: a stalled body is a timeout
    }
  }

  start(sessionId, { rootDomains, projectId }) {
    return this.request('POST', `/api/sessions/${encodeURIComponent(sessionId)}/capture/start`,
      { rootDomains, projectId: projectId || null });
  }

  end(sessionId, { runId, observations }) {
    return this.request('POST', `/api/sessions/${encodeURIComponent(sessionId)}/capture/end`,
      { runId, observations: observations || [] });
  }

  // Sessions whose scope holds `scope` (signed-in only), each with its largest capture.
  lookup(scope) {
    return this.request('GET', `/api/capture/sessions?scope=${encodeURIComponent(scope)}`);
  }

  // Continue adopts a session started in the workspace (ADR-0012): the server mints its extension id,
  // once. 409 already_adopted / conflict / archived means another extension or the workspace got there.
  adopt(platformSessionId) {
    return this.request('POST', `/api/capture/sessions/${encodeURIComponent(platformSessionId)}/adopt`, {});
  }

  // Quarantined scripts of ONE undecided host (ADR-0010), held by the platform apart from the run.
  quarantineFiles({ sessionId, runId, host, files }) {
    return this.request('POST', '/api/quarantine/files', { sessionId, runId, host, files });
  }

  // The researcher's Add or Ignore for one host (the platform's session id, not the extension's).
  decideHost(platformSessionId, host, decision) {
    return this.request(
      'POST',
      `/sessions/${encodeURIComponent(platformSessionId)}/hosts/${encodeURIComponent(host)}/decision`,
      { decision },
    );
  }

  // The team's third-party rules (ADR-0010, R2): the merged list to download, and one host to add.
  teamRulesMerged() {
    return this.request('GET', '/settings/third-party-rules/merged');
  }

  addTeamRule(kind, value) {
    return this.request('POST', '/settings/third-party-rules', { kind, value });
  }

  addScope(sessionId, host) {
    return this.request('POST', `/api/sessions/${encodeURIComponent(sessionId)}/scope`, { add: host });
  }
}
