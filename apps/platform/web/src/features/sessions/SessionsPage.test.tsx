import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { SessionsPage } from "./SessionsPage";
import * as api from "../../api/apiClient";
import type { SessionSummary } from "../../api/types";

const navigate = vi.fn();
vi.mock("react-router", async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

const DONE: SessionSummary = {
  session_id: "s1", name: null, host: "acme.io", scope_hosts: ["acme.io"],
  engagement_id: null, archived: false, created_at: "2026-08-01T00:00:00Z",
  latest_run: { run_id: "r1", state: "done", created_at: "2026-08-01T00:00:00Z", started_at: "2026-08-01T00:00:00Z", ended_at: "2026-08-01T00:05:00Z", target: null },
  files: 3, endpoints: 5, secrets: 1, coverage_pct: 80,
};
const NORUN: SessionSummary = {
  session_id: "s2", name: null, host: "shop.test", scope_hosts: ["shop.test"],
  engagement_id: null, archived: false, created_at: "2026-08-01T00:00:00Z",
  latest_run: null, files: null, endpoints: null, secrets: null, coverage_pct: null,
};

beforeEach(() => { vi.restoreAllMocks(); navigate.mockClear(); });

function renderPage() {
  return render(<MemoryRouter><SessionsPage tenantId="t1" /></MemoryRouter>);
}

describe("SessionsPage", () => {
  it("renders a card per session with real stats and honest '—' for missing ones", async () => {
    vi.spyOn(api, "listSessions").mockResolvedValue({ count: 2, sessions: [DONE, NORUN] });
    renderPage();
    expect(await screen.findByText("acme.io")).toBeInTheDocument();
    expect(screen.getByText("shop.test")).toBeInTheDocument();
    // real coverage renders "% attributed" (never "% analyzed"); missing renders "—"
    expect(screen.getByText("80% attributed")).toBeInTheDocument();
    expect(screen.getByText("— attributed")).toBeInTheDocument();
    // a run-less session shows "no runs", never faked zeros
    expect(screen.getByText("no runs")).toBeInTheDocument();
  });

  it("re-runs a session from the kebab menu and navigates to the new run", async () => {
    vi.spyOn(api, "listSessions").mockResolvedValue({ count: 1, sessions: [DONE] });
    vi.spyOn(api, "rerunSession").mockResolvedValue({ run_id: "r-new", state: "queued" });
    renderPage();
    await screen.findByText("acme.io");
    await userEvent.click(screen.getByRole("button", { name: /session actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /re-run/i }));
    expect(api.rerunSession).toHaveBeenCalledWith("t1", "s1");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/runs/r-new"));
  });

  it("toggles archived and refetches with the archived flag", async () => {
    const spy = vi.spyOn(api, "listSessions").mockResolvedValue({ count: 0, sessions: [] });
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalledWith("t1", { archived: false }));
    await userEvent.click(screen.getByLabelText(/show archived/i));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("t1", { archived: true }));
  });

  it("opens a session's latest run when its card is clicked", async () => {
    vi.spyOn(api, "listSessions").mockResolvedValue({ count: 1, sessions: [DONE] });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /open acme\.io/i }));
    expect(navigate).toHaveBeenCalledWith("/runs/r1");
  });
});
