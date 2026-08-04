// api.js — fetch wrappers around the FastAPI endpoints the workspace consumes.
// Same-origin (the SPA is served by FastAPI), so relative paths are fine.

// A stuck endpoint must never hang a request forever: the workspace boots from a
// Promise of several GETs, so a call that never resolves would stall the shell.
// Abort after a bounded wait → null. Light list endpoints settle in well under the
// fast budget; the analysis/reconstruction endpoints re-process source maps per call
// and legitimately take longer, so they pass a generous budget that still bounds a
// true hang (e.g. a wedged single-worker API).
const FAST_TIMEOUT_MS = 20000;
const HEAVY_TIMEOUT_MS = 90000;

async function getJson(path, timeoutMs = FAST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(path, { headers: { Accept: 'application/json' }, signal: controller.signal });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export const getHealth = () => getJson('/health');
export const getSessions = () => getJson('/api/sessions');
// Slim list (no per-asset arrays): polled every few seconds, and the workspace only
// needs status/coverage/targets. The full per-asset detail is large and unused here.
export const getReconJobs = () => getJson('/api/recon/jobs?include_assets=false');
export const getComprehensiveAnalysis = (sessionId) => getJson(`/api/sessions/${sessionId}/comprehensive-analysis`, HEAVY_TIMEOUT_MS);
// Graph assembly can be non-trivial for large sessions, so use the generous budget.
export const getAssetGraph = (sessionId) => getJson(`/api/sessions/${sessionId}/asset-graph`, HEAVY_TIMEOUT_MS);
// The file list carries per-file source-map metadata and can be slow for large
// sessions on the single-worker API, so it uses the generous budget.
export const getSessionFiles = (sessionId) => getJson(`/api/sessions/${sessionId}/files`, HEAVY_TIMEOUT_MS);

// Sources view (UI-002 Phase 3). reconstructed-sources re-processes the map per
// call, so callers fetch it lazily per bundle (and on the heavy timeout budget).
// getFileContent returns the raw file body as text (or null on 404/410/error) for
// bundles without reconstructed sources.
export const getReconstructedSources = (fileId) => getJson(`/api/files/${fileId}/reconstructed-sources`, HEAVY_TIMEOUT_MS);

export async function getFileContent(fileId) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HEAVY_TIMEOUT_MS);
  try {
    const resp = await fetch(`/api/files/${fileId}/content`, { headers: { Accept: 'text/plain' }, signal: controller.signal });
    if (!resp.ok) return null;
    return await resp.text();
  } catch (e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// Recon jobs (UI-002 Phase 4). start returns { success, jobId, sessionId, job }.
// stop returns { success, stopRequested, job }. Both null on error so callers can flash.
export async function startReconJob(payload) {
  try {
    const resp = await fetch('/api/recon/jobs/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload)
    });
    const body = await resp.json().catch(() => null);
    if (!resp.ok) return { ok: false, error: (body && body.detail) || `HTTP ${resp.status}` };
    return { ok: true, ...body };
  } catch (e) {
    return { ok: false, error: 'Network error — is the API reachable?' };
  }
}

export async function stopReconJob(jobId) {
  try {
    const resp = await fetch(`/api/recon/jobs/${jobId}/stop`, { method: 'POST', headers: { Accept: 'application/json' } });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    return null;
  }
}

// Session management. delete → DELETE /api/sessions/{id}; rename → PATCH with {name}.
// Both return null on error so callers can flash a message and roll back optimistic UI.
export async function deleteSession(sessionId) {
  try {
    const resp = await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE', headers: { Accept: 'application/json' } });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    return null;
  }
}

export const renameSession = (sessionId, name) => patchSession(sessionId, { name });
export const setSessionScope = (sessionId, rootDomains, includeSubdomains) =>
  patchSession(sessionId, { rootDomains, includeSubdomains });
// Move a session into an engagement, or unassign it (projectId null → Standalone).
// null on error (incl. 404 for an unknown project) so callers roll back optimistic UI.
export const setSessionProject = (sessionId, projectId) =>
  patchSession(sessionId, { projectId: projectId || null });

// PATCH /api/sessions/{id} — partial update (name and/or scope). null on error.
async function patchSession(sessionId, patch) {
  try {
    const resp = await fetch(`/api/sessions/${sessionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(patch)
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    return null;
  }
}

// Projects (engagements). CRUD over /api/projects. getProjects returns an array (or null
// on error). create returns { ok, project|error }; rename/rescope return the project body
// (or null); delete returns { success } (or null) — mirroring the session helpers so callers
// can flash + roll back optimistic UI.
export const getProjects = () => getJson('/api/projects');

export async function createProject(name, rootDomains) {
  try {
    const resp = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ name, defaults: { scope: { rootDomains: rootDomains || [] } } })
    });
    const body = await resp.json().catch(() => null);
    if (!resp.ok) return { ok: false, error: (body && body.detail) || `HTTP ${resp.status}` };
    return { ok: true, project: body };
  } catch (e) {
    return { ok: false, error: 'Network error — is the API reachable?' };
  }
}

export const renameProject = (projectId, name) => patchProject(projectId, { name });
export const setProjectScope = (projectId, rootDomains) =>
  patchProject(projectId, { defaults: { scope: { rootDomains } } });

async function patchProject(projectId, patch) {
  try {
    const resp = await fetch(`/api/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(patch)
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    return null;
  }
}

export async function deleteProject(projectId) {
  try {
    const resp = await fetch(`/api/projects/${projectId}`, { method: 'DELETE', headers: { Accept: 'application/json' } });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    return null;
  }
}

// Finding triage status (UI-002 Phase 2). GET returns { statuses: {fingerprint: status} }.
export const getFindingStatuses = (sessionId) => getJson(`/api/sessions/${sessionId}/finding-status`);

export async function setFindingStatus(sessionId, fingerprint, status) {
  try {
    const resp = await fetch(`/api/sessions/${sessionId}/finding-status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ fingerprint, status })
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    return null;
  }
}
