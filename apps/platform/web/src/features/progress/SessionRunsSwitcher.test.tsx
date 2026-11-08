import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { SessionRunsSwitcher } from "./SessionRunsSwitcher";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { SessionRunRef } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function runRef(id: string, state: string, endpoints: number | null = 0): SessionRunRef {
  return {
    run_id: id, state, created_at: "2026-08-01T00:00:00Z", started_at: null, ended_at: null,
    target: null, files: endpoints === null ? null : 3, endpoints,
  };
}
function mount(runId: string, sessionId: string | null) {
  return render(
    <TenantProvider><MemoryRouter>
      <SessionRunsSwitcher runId={runId} sessionId={sessionId} />
    </MemoryRouter></TenantProvider>,
  );
}
function mockRuns(runs: SessionRunRef[]) {
  return vi.spyOn(api, "getSessionRuns").mockResolvedValue({ session_id: "sess", count: runs.length, runs });
}

describe("SessionRunsSwitcher", () => {
  it("lists every run, linking the others and marking the current one", async () => {
    mockRuns([runRef("run-aaaa1111", "done", 544), runRef("run-bbbb2222", "cancelled", 27)]);
    mount("run-bbbb2222", "sess");
    expect(await screen.findByText(/2 runs in this session/i)).toBeInTheDocument();
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/runs/run-aaaa1111");
    expect(screen.getByText(/this run/i)).toBeInTheDocument();
  });

  it("shows each run's endpoints with a bar scaled to the session's largest run", async () => {
    mockRuns([runRef("run-small", "done", 27), runRef("run-big", "done", 544)]);
    mount("run-small", "sess");
    const big = (await screen.findByText("544 endpoints")).closest("li")!;
    const small = screen.getByText("27 endpoints").closest("li")!;
    expect(within(big).getByRole("meter")).toHaveAttribute("aria-valuenow", "544");
    expect(within(big).getByRole("meter")).toHaveAttribute("aria-valuemax", "544");
    expect(within(small).getByRole("meter")).toHaveAttribute("aria-valuenow", "27");
  });

  it("never shows run IDs, but a square button copies each one", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mockRuns([runRef("run-aaaa1111", "done", 5), runRef("run-bbbb2222", "done", 9)]);
    mount("run-bbbb2222", "sess");
    await screen.findByText("5 endpoints");
    expect(screen.queryByText(/run-aaaa/)).toBeNull();

    const copy = screen.getAllByRole("button", { name: /copy run id/i });
    expect(copy).toHaveLength(2);
    await userEvent.click(copy[0]);

    expect(writeText).toHaveBeenCalledWith("run-aaaa1111");
    expect(await screen.findByRole("status")).toHaveTextContent(/copied/i);
  });

  it("marks a capture that is still open", async () => {
    mockRuns([runRef("run-live", "capturing", 12), runRef("run-old", "done", 9)]);
    mount("run-old", "sess");
    expect(await screen.findByText(/capturing/i)).toBeInTheDocument();
  });

  it("says unknown, not zero, when a run's count wasn't computed", async () => {
    mockRuns([runRef("run-new", "done", 4), runRef("run-ancient", "done", null)]);
    mount("run-new", "sess");
    const ancient = (await screen.findByText("— endpoints")).closest("li")!;
    expect(within(ancient).queryByRole("meter")).toBeNull();
  });

  it("renders nothing for a single-run session", async () => {
    const spy = mockRuns([runRef("run-only", "done")]);
    const { container } = mount("run-only", "sess");
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container.querySelector(".srs")).toBeNull();
  });

  it("does not fetch without a session id (older status snapshot)", () => {
    const spy = vi.spyOn(api, "getSessionRuns");
    mount("r", null);
    expect(spy).not.toHaveBeenCalled();
  });

  it("refetches when this run comes to rest or finishes, and trusts the live state", async () => {
    const spy = mockRuns([runRef("run-b", "capturing", 3), runRef("run-a", "done", 7)]);
    const view = render(
      <TenantProvider><MemoryRouter>
        <SessionRunsSwitcher runId="run-b" sessionId="sess" runState="analyzing" />
      </MemoryRouter></TenantProvider>,
    );
    await screen.findByText("3 endpoints");
    spy.mockResolvedValue({ session_id: "sess", count: 2, runs: [runRef("run-b", "done", 5), runRef("run-a", "done", 7)] });
    view.rerender(
      <TenantProvider><MemoryRouter>
        <SessionRunsSwitcher runId="run-b" sessionId="sess" runState="done" />
      </MemoryRouter></TenantProvider>,
    );
    expect(await screen.findByText("5 endpoints")).toBeInTheDocument();
    expect(screen.queryByText(/^capturing$/)).toBeNull();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("names every capture uniquely and draws no meter when nothing was found", async () => {
    mockRuns([runRef("run-b", "done", 0), runRef("run-a", "done", 0)]);
    mount("run-b", "sess");
    await screen.findAllByText("0 endpoints");
    expect(screen.queryByRole("meter")).toBeNull();
    const names = screen.getAllByRole("button", { name: /copy run id/i }).map((b) => b.getAttribute("aria-label"));
    expect(new Set(names).size).toBe(2);
    expect(names[0]).toMatch(/capture 2/i);  // newest first
  });

  it("shows the run ID when the browser blocks copying", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    mockRuns([runRef("run-aaaa1111", "done", 5), runRef("run-bbbb2222", "done", 9)]);
    mount("run-bbbb2222", "sess");
    await screen.findByText("5 endpoints");
    await userEvent.click(screen.getAllByRole("button", { name: /copy run id/i })[0]);
    expect(await screen.findByRole("status")).toHaveTextContent("run-aaaa1111");
  });
});
