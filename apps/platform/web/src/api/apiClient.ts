import type {
  AssetsManifest, BaseUrlRule, BaseUrlRuleResult, Engagement, EngagementsListResponse, FindingsResponse, RequestsResponse, RunRef, RunStatus, RunControlResult, SessionDetail, SessionsListResponse, SessionRunsResponse, SessionView, SourceContent, SourcesResponse, SpecSummary, Triage, WrapperRule, WrapperRuleResult,
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

async function request<T>(path: string, init: RequestInit, tenantId: string): Promise<T> {
  const headers: Record<string, string> = {
    "X-Tenant-Id": tenantId,
    Accept: "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) throw new ApiError(res.status, await readErrorDetail(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
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
  tenantId: string, body: { session_id: string; target: string; capture?: boolean },
): Promise<RunRef> {
  return request("/runs", json("POST", body), tenantId);
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

export function getFindings(tenantId: string, runId: string): Promise<FindingsResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/findings`, {}, tenantId);
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

export function getRequests(tenantId: string, runId: string): Promise<RequestsResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/requests`, {}, tenantId);
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
    { headers: { "X-Tenant-Id": tenantId } },
  );
  if (!res.ok) throw new ApiError(res.status, await readErrorDetail(res));
  return res.blob();
}
