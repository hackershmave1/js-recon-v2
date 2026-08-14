import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { useCaptureDeepLink } from "./app";
import { TenantProvider } from "./tenant/TenantContext";
import * as api from "./api/apiClient";
import type { SessionSummary } from "./api/types";

const navigate = vi.fn();
vi.mock("react-router", async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); navigate.mockClear(); localStorage.setItem("recon.tenantId", TENANT); });

function session(overrides: Partial<SessionSummary>): SessionSummary {
  return {
    session_id: "s1", external_id: "ext-1", name: null, host: "acme.io", scope_hosts: [],
    engagement_id: null, archived: false, created_at: null,
    latest_run: { run_id: "r1", state: "done", created_at: null, started_at: null, ended_at: null, target: null },
    files: 1, endpoints: 0, secrets: 0, coverage_pct: null, ...overrides,
  };
}
function Harness() { useCaptureDeepLink(); return <div>home</div>; }
function renderAt(entry: string) {
  return render(
    <TenantProvider><MemoryRouter initialEntries={[entry]}><Harness /></MemoryRouter></TenantProvider>,
  );
}

describe("useCaptureDeepLink", () => {
  it("jumps to the matching capture session's latest run", async () => {
    vi.spyOn(api, "listSessions").mockResolvedValue({ count: 1, sessions: [session({})] });
    renderAt("/?capture=ext-1");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/runs/r1", { replace: true }));
  });

  it("falls back to /sessions when no session matches the capture id", async () => {
    vi.spyOn(api, "listSessions").mockResolvedValue({ count: 0, sessions: [] });
    renderAt("/?capture=ext-unknown");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/sessions", { replace: true }));
  });

  it("does nothing (and never fetches) without a capture param", async () => {
    const spy = vi.spyOn(api, "listSessions");
    renderAt("/");
    await Promise.resolve();
    expect(spy).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("falls back to /sessions when the matched session has no run yet", async () => {
    vi.spyOn(api, "listSessions").mockResolvedValue({ count: 1, sessions: [session({ latest_run: null })] });
    renderAt("/?capture=ext-1");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/sessions", { replace: true }));
  });

  it("falls back to /sessions when the session list fetch fails", async () => {
    vi.spyOn(api, "listSessions").mockRejectedValue(new Error("boom"));
    renderAt("/?capture=ext-1");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/sessions", { replace: true }));
  });
});
