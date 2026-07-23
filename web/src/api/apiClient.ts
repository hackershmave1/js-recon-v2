import type { FindingsResponse, RunRef, RunStatus, SessionView, Triage } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit, tenantId: string): Promise<T> {
  const headers: Record<string, string> = {
    "X-Tenant-Id": tenantId,
    Accept: "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON body */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function createSession(
  tenantId: string, body: { scope_hosts: string[]; authorized_by: string; name?: string },
): Promise<SessionView> {
  return request("/sessions", json("POST", body), tenantId);
}

export function uploadRun(tenantId: string, form: FormData): Promise<RunRef> {
  return request("/runs/upload", { method: "POST", body: form }, tenantId);
}

export function getStatus(tenantId: string, runId: string): Promise<RunStatus> {
  return request(`/runs/${encodeURIComponent(runId)}/status`, {}, tenantId);
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
