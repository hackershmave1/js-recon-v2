import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { SessionRunsSwitcher } from "./SessionRunsSwitcher";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { SessionRunRef } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function runRef(id: string, state: string): SessionRunRef {
  return { run_id: id, state, created_at: "2026-08-01T00:00:00Z", started_at: null, ended_at: null, target: null };
}
function mount(runId: string, sessionId: string | null) {
  return render(
    <TenantProvider><MemoryRouter>
      <SessionRunsSwitcher runId={runId} sessionId={sessionId} />
    </MemoryRouter></TenantProvider>,
  );
}

describe("SessionRunsSwitcher", () => {
  it("lists every run in the session, linking the others and marking the current one", async () => {
    vi.spyOn(api, "getSessionRuns").mockResolvedValue({
      session_id: "sess", count: 2,
      runs: [runRef("run-aaaa1111", "done"), runRef("run-bbbb2222", "cancelled")],
    });
    mount("run-bbbb2222", "sess");
    expect(await screen.findByText(/2 runs in this session/i)).toBeInTheDocument();
    // the OTHER round is a link back to its run; the current round is plain text
    expect(screen.getByRole("link", { name: /run-aaaa/i })).toHaveAttribute("href", "/runs/run-aaaa1111");
    expect(screen.queryByRole("link", { name: /run-bbbb/i })).toBeNull();
    expect(screen.getByText(/this run/i)).toBeInTheDocument();
  });

  it("renders nothing for a single-run session", async () => {
    const spy = vi.spyOn(api, "getSessionRuns").mockResolvedValue({
      session_id: "sess", count: 1, runs: [runRef("run-only", "done")],
    });
    const { container } = mount("run-only", "sess");
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container.querySelector(".srs")).toBeNull();
  });

  it("does not fetch without a session id (older status snapshot)", () => {
    const spy = vi.spyOn(api, "getSessionRuns");
    mount("r", null);
    expect(spy).not.toHaveBeenCalled();
  });
});
