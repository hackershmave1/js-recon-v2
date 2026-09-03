import type {
  AssetsManifest, BaseUrlRule, BaseUrlRuleResult, Engagement, EngagementsListResponse, FindingsResponse, HostsResponse, RequestsResponse, RunConfig, RunRef, RunStatus, RunControlResult, SessionDetail, SessionsListResponse, SessionRunsResponse, SessionView, SourceContent, SourcesResponse, SourceSearchResponse, SpecSummary, TechnologiesResponse, Triage, WrapperRule, WrapperRuleResult,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

async function readErrorDetail(res: Response): Promise<string> {
  let detail = `HTTP ${res.status}`;
  try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON body */ }
  return detail;
}

// The login session token (recon.auth) lives in localStorage and rides every request
// as `Authorization: Bearer`, read at call time so a login/logout is picked up without
// re-wiring callers. Absent (logged out, or auth disabled server-side) => no header,
// and the server falls back to the X-Tenant-Id stand-in (dev/test) or 401s.
export const AUTH_TOKEN_KEY = "recon.authToken";

function authHeader(): Record<string, string> {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// A 401 from any tenant call means the session is gone (an expired/rotated token). The app
// registers a handler here so it can drop to the login screen instead of looping on 401s
// forever. Login/getMe deliberately DON'T trigger it — a bad login isn't a lost session.
let unauthorizedHandler: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}
export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

async function request<T>(path: string, init: RequestInit, tenantId: string): Promise<T> {
  const headers: Record<string, string> = {
    "X-Tenant-Id": tenantId,
    Accept: "application/json",
    ...authHeader(),
    ...(init.headers as Record<string, string> | undefined),
  };
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    if (res.status === 401) notifyUnauthorized();
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// --- Authentication -------------------------------------------------------- //

export interface AuthTenant { id: string; name: string | null }
export interface LoginResult { token: string; user: string; role: string; tenant: AuthTenant }
export interface MeResult { user_id: string; role: string; tenant: AuthTenant }

// POST /auth/login — pre-tenant, pre-auth (no X-Tenant-Id, no Bearer). Throws ApiError
// on failure (401 invalid credentials, 503 auth not configured on the server).
export async function login(username: string, password: string): Promise<LoginResult> {
  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new ApiError(res.status, await readErrorDetail(res));
  return res.json() as Promise<LoginResult>;
}

// GET /auth/me — re-validate the stored token server-side (its HMAC signature can't be
// checked in the browser). A 401 means the token is stale/rejected and the caller logs out.
export async function getMe(): Promise<MeResult> {
  const res = await fetch("/auth/me", { headers: { Accept: "application/json", ...authHeader() } });
  if (!res.ok) throw new ApiError(res.status, await readErrorDetail(res));
  return res.json() as Promise<MeResult>;
}

export function createSession(
  tenantId: string,
  // `target` (a crawl domain) lets the backend seed scope when scope_hosts is
  // blank (S3), so the New Recon form need not make the user retype the domain.
  body: { scope_hosts: string[]; authorized_by: string; name?: string; engagement_id?: string; target?: string },
): Promise<SessionView> {
  return request("/sessions", json("POST", body), tenantId);
}

// --- R6 Sessions surface --------------------------------------------------- //

export function listSessions(
  tenantId: string, opts: { archived?: boolean } = {},
): Promise<SessionsListResponse> {
  return request(`/sessions${opts.archived ? "?archived=true" : ""}`, {}, tenantId);
}

export function getSessionRuns(tenantId: string, sessionId: string): Promise<SessionRunsResponse> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/runs`, {}, tenantId);
}

export function renameSession(tenantId: string, sessionId: string, name: string): Promise<SessionDetail> {
  return request(`/sessions/${encodeURIComponent(sessionId)}`, json("PATCH", { name }), tenantId);
}

export function archiveSession(tenantId: string, sessionId: string, archived: boolean): Promise<SessionDetail> {
  return request(`/sessions/${encodeURIComponent(sessionId)}`, json("PATCH", { archived }), tenantId);
}

export function deleteSession(tenantId: string, sessionId: string): Promise<void> {
  return request(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }, tenantId);
}

// Re-run reproduces the session's latest run (crawl re-fetch / upload re-analyze) as a
// new run under the same session; returns the new RunRef, like POST /runs.
export function rerunSession(tenantId: string, sessionId: string): Promise<RunRef> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/rerun`, { method: "POST" }, tenantId);
}

// --- R6 Engagement tier ---------------------------------------------------- //

export function listEngagements(tenantId: string): Promise<EngagementsListResponse> {
  return request("/engagements", {}, tenantId);
}

export function createEngagement(
  tenantId: string,
  body: { name: string; in_scope_domains: string[]; out_of_scope_domains: string[] },
): Promise<Engagement> {
  return request("/engagements", json("POST", body), tenantId);
}

export function uploadRun(tenantId: string, form: FormData): Promise<RunRef> {
  return request("/runs/upload", { method: "POST", body: form }, tenantId);
}

export function startRun(
  // `capture` opts the run into the runtime CDP capture stage (executed JS: workers,
  // injected, eval'd). Omitted unless true; the server 400s if capture mode is off.
  // `scan_suspected` opts into the D33-B low-confidence "suspected secret" recall lane.
  tenantId: string,
  body: { session_id: string; target: string; capture?: boolean; scan_suspected?: boolean },
): Promise<RunRef> {
  return request("/runs", json("POST", body), tenantId);
}

// The source run's editable config, for the edit-&-re-run prefill (GET /runs/{id}/config).
export function getRunConfig(tenantId: string, runId: string): Promise<RunConfig> {
  return request(`/runs/${encodeURIComponent(runId)}/config`, {}, tenantId);
}

// Edit a specific run's config and launch a NEW run inheriting it (POST /runs/{id}/rerun).
// Only send fields the operator changed from the prefill; unsent fields inherit. A scope
// change forks a fresh session and REQUIRES `authorized_by` (re-attest the widened scope).
export function editAndRerun(
  tenantId: string, runId: string,
  body: {
    target?: string; capture?: boolean; scope_hosts?: string[];
    authorized_by?: string; max_fetch_bytes?: number; scan_suspected?: boolean;
  },
): Promise<RunRef> {
  return request(`/runs/${encodeURIComponent(runId)}/rerun`, json("POST", body), tenantId);
}

export function getAssets(tenantId: string, runId: string): Promise<AssetsManifest> {
  return request(`/runs/${encodeURIComponent(runId)}/assets`, {}, tenantId);
}

export function getStatus(tenantId: string, runId: string): Promise<RunStatus> {
  return request(`/runs/${encodeURIComponent(runId)}/status`, {}, tenantId);
}

export function getSources(tenantId: string, runId: string): Promise<SourcesResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/sources`, {}, tenantId);
}

// D52: run-scoped full-text grep across the run's sources (bounded server-side).
export function searchSources(tenantId: string, runId: string, query: string): Promise<SourceSearchResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/sources/search?q=${encodeURIComponent(query)}`, {}, tenantId);
}

// `assetUrl` disambiguates a source-map-recovered file (kind:"source") that shares
// a path across two assets; omit it for asset/upload files (server treats it as
// optional). See recon.probe.sources.
export function getSourceContent(
  tenantId: string, runId: string, path: string, assetUrl?: string | null,
): Promise<SourceContent> {
  const asset = assetUrl != null ? `&asset_url=${encodeURIComponent(assetUrl)}` : "";
  return request(
    `/runs/${encodeURIComponent(runId)}/sources/content?path=${encodeURIComponent(path)}${asset}`,
    {}, tenantId,
  );
}

export function getFindings(
  tenantId: string,
  runId: string,
  includeNoise = false,
): Promise<FindingsResponse> {
  // #3: analytics/telemetry/vendor hosts are hidden by default; include_noise=true shows them.
  const q = includeNoise ? "?include_noise=true" : "";
  return request(`/runs/${encodeURIComponent(runId)}/findings${q}`, {}, tenantId);
}

export function getTechnologies(tenantId: string, runId: string): Promise<TechnologiesResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/technologies`, {}, tenantId);
}

export function getHosts(tenantId: string, runId: string): Promise<HostsResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/hosts`, {}, tenantId);
}

export function triageFinding(
  tenantId: string, runId: string, hash: string,
  body: { status: string; note?: string; actor?: string },
): Promise<Triage & { finding_hash: string }> {
  return request(
    `/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(hash)}/triage`,
    json("POST", body), tenantId,
  );
}

export function revealSecret(
  tenantId: string, runId: string, hash: string, body: { actor?: string; reason?: string } = {},
): Promise<{ finding_hash: string; value: string }> {
  return request(
    `/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(hash)}/reveal`,
    json("POST", body), tenantId,
  );
}

// Raw body, not JSON-wrapped: the spec router reads the request body verbatim
// (JSON or YAML text, whatever the caller uploaded/pasted) regardless of
// Content-Type, mirroring uploadRun's non-JSON POST shape.
export function attachSpec(tenantId: string, runId: string, body: string | File): Promise<SpecSummary> {
  return request(`/runs/${encodeURIComponent(runId)}/spec`, { method: "POST", body }, tenantId);
}

export function listBaseUrlRules(tenantId: string, runId: string): Promise<BaseUrlRule[]> {
  return request(`/runs/${encodeURIComponent(runId)}/base-url`, {}, tenantId);
}

export function addBaseUrlRule(
  tenantId: string, runId: string,
  body: { kind: "prefix" | "selection"; base_url: string; path_prefix?: string; finding_hashes?: string[] },
): Promise<BaseUrlRuleResult> {
  return request(`/runs/${encodeURIComponent(runId)}/base-url`, json("POST", body), tenantId);
}

export function deleteBaseUrlRule(tenantId: string, runId: string, ruleId: string): Promise<void> {
  return request(
    `/runs/${encodeURIComponent(runId)}/base-url/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" }, tenantId,
  );
}

export function listWrapperRules(tenantId: string, runId: string): Promise<WrapperRule[]> {
  return request(`/runs/${encodeURIComponent(runId)}/wrappers`, {}, tenantId);
}

export function addWrapperRule(
  tenantId: string, runId: string, body: { callee: string; actor?: string },
): Promise<WrapperRuleResult> {
  return request(`/runs/${encodeURIComponent(runId)}/wrappers`, json("POST", body), tenantId);
}

export function deleteWrapperRule(tenantId: string, runId: string, ruleId: string): Promise<void> {
  return request(
    `/runs/${encodeURIComponent(runId)}/wrappers/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" }, tenantId,
  );
}

// `host` (optional, Starbucks QA #2) resolves host-less relative requests against a
// chosen host at probe time — the server re-serializes the curl/raw-HTTP through its
// hardened serializer, so the host is never assembled into a shell string client-side.
export function getRequests(tenantId: string, runId: string, host?: string): Promise<RequestsResponse> {
  const query = host ? `?host=${encodeURIComponent(host)}` : "";
  return request(`/runs/${encodeURIComponent(runId)}/requests${query}`, {}, tenantId);
}

export function pauseRun(tenantId: string, runId: string): Promise<RunControlResult> {
  return request(`/runs/${encodeURIComponent(runId)}/pause`, { method: "POST" }, tenantId);
}
export function cancelRun(tenantId: string, runId: string): Promise<RunControlResult> {
  return request(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }, tenantId);
}
export function resumeRun(tenantId: string, runId: string): Promise<RunControlResult> {
  return request(`/runs/${encodeURIComponent(runId)}/resume`, { method: "POST" }, tenantId);
}

// Blob variant: the export route streams a file (Content-Disposition), not JSON, so it
// bypasses request<T> (which forces Accept: application/json + res.json()). A bare
// <a href> can't carry X-Tenant-Id, so we fetch + trigger the download in JS.
export async function exportOpenApi(tenantId: string, runId: string, format: "json" | "yaml"): Promise<Blob> {
  const res = await fetch(
    `/runs/${encodeURIComponent(runId)}/export/openapi?format=${format}`,
    { headers: { "X-Tenant-Id": tenantId, ...authHeader() } },
  );
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    if (res.status === 401) notifyUnauthorized();
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}
