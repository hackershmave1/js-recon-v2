import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TenantProvider } from "../../tenant/TenantContext";
import { ScopePanel, expiryLabel } from "./ScopePanel";
import type { SessionScope } from "../../api/types";

vi.mock("../../api/apiClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/apiClient")>();
  return {
    ...actual,
    getSessionScope: vi.fn(),
    addScopeHost: vi.fn().mockResolvedValue({ scope_hosts: [] }),
    removeScopeHost: vi.fn().mockResolvedValue({ scope_hosts: [] }),
    decideHost: vi.fn().mockResolvedValue({ state: "added", promoted: 1, deleted: 1 }),
    deleteHostContent: vi.fn().mockResolvedValue({ files: 1, findings: 2 }),
  };
});
import * as api from "../../api/apiClient";

const soon = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000 - 60_000).toISOString();
const full: SessionScope = {
  scope_hosts: ["acme.io"],
  undecided: [{ host: "cdn.acme-static.com", files: 3, bytes: 900, first_seen_at: "2026-11-10T00:00:00Z", expires_at: soon }],
  ignored: ["ads.example.net"],
  hidden: [{ host: "old.acme-cdn.com", files: 2, findings: 4 }],
};

const show = (onChanged = () => {}) =>
  render(<TenantProvider><ScopePanel sessionId="s-1" onChanged={onChanged} /></TenantProvider>);

describe("ScopePanel", () => {
  beforeEach(() => {
    localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000");
    vi.clearAllMocks();
    vi.mocked(api.getSessionScope).mockResolvedValue(full);
  });

  it("shows the scope, the undecided hosts with their countdown, the ignore list and the hidden hosts", async () => {
    show();
    expect(await screen.findByText("acme.io")).toBeInTheDocument();
    expect(screen.getByText("cdn.acme-static.com")).toBeInTheDocument();
    expect(screen.getByText(/3 scripts · deleted in 5 days/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ignored" })).toBeInTheDocument();
    expect(screen.getByText("ads.example.net")).toBeInTheDocument();
    const hidden = screen.getByRole("region", { name: "Hidden: out of scope" });
    expect(within(hidden).getByText("old.acme-cdn.com")).toBeInTheDocument();
    expect(within(hidden).getByText(/2 files · 4 findings/)).toBeInTheDocument();
  });

  it("labels a crawl-referenced host 'referenced, not fetched' and says Add starts a crawl", async () => {
    vi.mocked(api.getSessionScope).mockResolvedValue({
      ...full,
      undecided: [{
        host: "cdn.acme-static.com", files: 0, bytes: 0, first_seen_at: "2026-11-10T00:00:00Z", expires_at: soon,
        referenced: true, referenced_urls: ["https://cdn.acme-static.com/lib.js", "https://cdn.acme-static.com/b.js"],
      }],
    });
    vi.mocked(api.decideHost).mockResolvedValue({
      state: "added", promoted: 0, deleted: 0, recrawl: { source_run_id: "r-1", run_id: "r-2" },
    });
    show();
    const group = await screen.findByRole("region", { name: "Waiting for your decision" });
    expect(within(group).getByText(/referenced, not fetched/)).toBeInTheDocument();
    expect(within(group).getByText(/2 scripts/)).toBeInTheDocument();
    await userEvent.click(within(group).getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/new crawl is fetching/i);
  });

  it("says what removing does: it hides and is reversible", async () => {
    show();
    await screen.findByText("acme.io");
    expect(screen.getByText(/removing one hides its files and findings/i)).toBeInTheDocument();
  });

  it("Add and Ignore on an undecided host call the decision endpoint, then reload and tell the page", async () => {
    const onChanged = vi.fn();
    show(onChanged);
    const group = await screen.findByRole("region", { name: "Waiting for your decision" });
    await userEvent.click(within(group).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(api.decideHost).toHaveBeenCalledWith(expect.any(String), "s-1", "cdn.acme-static.com", "add"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(api.getSessionScope).toHaveBeenCalledTimes(2); // initial + after the change
    await userEvent.click(within(group).getByRole("button", { name: "Ignore" }));
    await waitFor(() => expect(api.decideHost).toHaveBeenCalledWith(expect.any(String), "s-1", "cdn.acme-static.com", "ignore"));
  });

  it("Remove hides a host from the scope", async () => {
    const onChanged = vi.fn();
    show(onChanged);
    await userEvent.click(await screen.findByRole("button", { name: "Remove acme.io from scope" }));
    await waitFor(() => expect(api.removeScopeHost).toHaveBeenCalledWith(expect.any(String), "s-1", "acme.io"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("adds a typed host to the scope", async () => {
    show();
    await userEvent.type(await screen.findByLabelText("Add a host to the scope"), "acme.io");
    await userEvent.click(within(screen.getByRole("form", { name: "Add a host" })).getByRole("button", { name: "Add to scope" }));
    await waitFor(() => expect(api.addScopeHost).toHaveBeenCalledWith(expect.any(String), "s-1", "acme.io"));
  });

  it("adds an ignored host to the scope (it leaves the ignore list on the server)", async () => {
    show();
    const ignored = await screen.findByRole("region", { name: "Ignored hosts" });
    await userEvent.click(within(ignored).getByRole("button", { name: "Add to scope" }));
    await waitFor(() => expect(api.addScopeHost).toHaveBeenCalledWith(expect.any(String), "s-1", "ads.example.net"));
  });

  it("brings a hidden host back with Add back", async () => {
    show();
    const hidden = await screen.findByRole("region", { name: "Hidden: out of scope" });
    await userEvent.click(within(hidden).getByRole("button", { name: "Add back" }));
    await waitFor(() => expect(api.addScopeHost).toHaveBeenCalledWith(expect.any(String), "s-1", "old.acme-cdn.com"));
  });

  it("deleting content needs a second, explicit confirmation and can be cancelled", async () => {
    show();
    const hidden = await screen.findByRole("region", { name: "Hidden: out of scope" });
    await userEvent.click(within(hidden).getByRole("button", { name: "Delete content" }));
    expect(api.deleteHostContent).not.toHaveBeenCalled();
    expect(within(hidden).getByText(/Delete 2 files and the findings seen only in them\? This can't be undone\./)).toBeInTheDocument();
    await userEvent.click(within(hidden).getByRole("button", { name: "Cancel" }));
    expect(within(hidden).getByRole("button", { name: "Delete content" })).toBeInTheDocument();
    await userEvent.click(within(hidden).getByRole("button", { name: "Delete content" }));
    await userEvent.click(within(hidden).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.deleteHostContent).toHaveBeenCalledWith(expect.any(String), "s-1", "old.acme-cdn.com"));
  });

  it("omits the groups that are empty", async () => {
    vi.mocked(api.getSessionScope).mockResolvedValue({ scope_hosts: ["acme.io"], undecided: [], ignored: [], hidden: [] });
    show();
    await screen.findByText("acme.io");
    expect(screen.queryByRole("region", { name: "Hidden: out of scope" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Waiting for your decision" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Ignored hosts" })).toBeNull();
  });

  it("reports a failed change instead of failing silently", async () => {
    vi.mocked(api.removeScopeHost).mockRejectedValueOnce(new Error("boom"));
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Remove acme.io from scope" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't remove acme.io.");
  });
});

describe("Add after a capture ended", () => {
  beforeEach(() => {
    localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000");
    vi.clearAllMocks();
    vi.mocked(api.getSessionScope).mockResolvedValue(full);
  });

  it("says the scripts are analyzed in a new run (no button to press)", async () => {
    vi.mocked(api.decideHost).mockResolvedValueOnce({ state: "added", promoted: 1, deleted: 1, analysis: { "run-1": "followup:run-2" } });
    show();
    const group = await screen.findByRole("region", { name: "Waiting for your decision" });
    await userEvent.click(within(group).getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/being analyzed in a new run of this session/);
  });

  it("reports a follow-up that could not start", async () => {
    vi.mocked(api.decideHost).mockResolvedValueOnce({ state: "added", promoted: 1, deleted: 1, analysis: { "run-1": "needs_rerun" } });
    show();
    const group = await screen.findByRole("region", { name: "Waiting for your decision" });
    await userEvent.click(within(group).getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/analysis didn't start/);
  });

  it("says nothing extra when the run was still open (scheduled)", async () => {
    vi.mocked(api.decideHost).mockResolvedValueOnce({ state: "added", promoted: 1, deleted: 1, analysis: { "run-1": "scheduled" } });
    show();
    const group = await screen.findByRole("region", { name: "Waiting for your decision" });
    await userEvent.click(within(group).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(api.decideHost).toHaveBeenCalled());
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("expiryLabel", () => {
  const now = Date.parse("2026-11-16T00:00:00Z");
  it("counts the days until held scripts are deleted", () => {
    expect(expiryLabel("2026-11-21T00:00:00Z", now)).toBe("deleted in 5 days");
    expect(expiryLabel("2026-11-16T10:00:00Z", now)).toBe("deleted within a day");
    expect(expiryLabel("2026-11-15T23:00:00Z", now)).toBe("expired");
    expect(expiryLabel(null, now)).toBe("");
  });
});
