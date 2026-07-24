import { describe, it, expect, vi, beforeEach } from "vitest";
import { ApiError, getFindings, createSession } from "./apiClient";

beforeEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

describe("apiClient", () => {
  it("injects the X-Tenant-Id badge and Accept: application/json", async () => {
    const f = mockFetch(200, { run_id: "r1", count: 0, coverage: null, findings: [] });
    vi.stubGlobal("fetch", f);
    await getFindings("tenant-1", "r1");
    const [, init] = f.mock.calls[0];
    expect((init.headers as Record<string, string>)["X-Tenant-Id"]).toBe("tenant-1");
    expect((init.headers as Record<string, string>)["Accept"]).toBe("application/json");
  });

  it("throws ApiError with status + detail on non-2xx", async () => {
    vi.stubGlobal("fetch", mockFetch(404, { detail: "run not found" }));
    await expect(getFindings("t", "missing")).rejects.toMatchObject({
      status: 404, message: "run not found",
    });
    await expect(getFindings("t", "missing")).rejects.toBeInstanceOf(ApiError);
  });

  it("sends JSON body for createSession", async () => {
    const f = mockFetch(201, { session_id: "s1", scope_hosts: ["a"], authorization_ack: true });
    vi.stubGlobal("fetch", f);
    await createSession("t", { scope_hosts: ["a"], authorized_by: "me" });
    const [path, init] = f.mock.calls[0];
    expect(path).toBe("/sessions");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ scope_hosts: ["a"], authorized_by: "me" });
  });
});
