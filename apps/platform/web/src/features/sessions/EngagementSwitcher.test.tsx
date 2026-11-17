import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EngagementFilterProvider } from "./EngagementFilterProvider";
import { EngagementSwitcher } from "./EngagementSwitcher";
import { ENGAGEMENT_STORAGE_KEY } from "./engagementFilter";
import { detachMessage } from "./DeleteEngagementConfirm";
import { TenantProvider } from "../../tenant/TenantContext";
import type { Engagement } from "../../api/types";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";

function engagement(id: string, name: string, sessionCount: number): Engagement {
  return {
    engagement_id: id, name, in_scope_domains: [], out_of_scope_domains: [],
    created_at: "2026-11-17T00:00:00Z", updated_at: "2026-11-17T00:00:00Z",
    session_count: sessionCount,
  };
}
const ACME = engagement("eng-acme", "Acme Q3", 2);
const OTHER = engagement("eng-other", "Other", 0);

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  localStorage.setItem("recon.tenantId", TENANT);
});

function renderSwitcher() {
  render(
    <TenantProvider>
      <EngagementFilterProvider>
        <EngagementSwitcher />
      </EngagementFilterProvider>
    </TenantProvider>,
  );
}

describe("EngagementSwitcher delete (#262)", () => {
  it("confirms by name and count, deletes, refreshes, and falls a selection back to All", async () => {
    localStorage.setItem(ENGAGEMENT_STORAGE_KEY, ACME.engagement_id); // Acme is selected
    const list = vi.spyOn(api, "listEngagements")
      .mockResolvedValueOnce({ count: 2, engagements: [ACME, OTHER] })
      .mockResolvedValue({ count: 1, engagements: [OTHER] });
    const del = vi.spyOn(api, "deleteEngagement")
      .mockResolvedValue({ engagement_id: ACME.engagement_id, detached_sessions: 2 });
    renderSwitcher();

    await userEvent.click(await screen.findByRole("button", { name: /acme q3/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete engagement Acme Q3" }));
    const dialog = screen.getByRole("dialog", { name: 'Delete engagement "Acme Q3"?' });
    expect(dialog).toHaveTextContent("2 sessions will move to No engagement; nothing is deleted.");

    await userEvent.click(screen.getByRole("button", { name: "Delete engagement" }));
    expect(del).toHaveBeenCalledWith(TENANT, ACME.engagement_id);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: /all engagements/i })).toBeInTheDocument();
    expect(localStorage.getItem(ENGAGEMENT_STORAGE_KEY)).toBeNull();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancel deletes nothing", async () => {
    vi.spyOn(api, "listEngagements").mockResolvedValue({ count: 1, engagements: [ACME] });
    const del = vi.spyOn(api, "deleteEngagement");
    renderSwitcher();
    await userEvent.click(await screen.findByRole("button", { name: /all engagements/i }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete engagement Acme Q3" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(del).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a failed delete keeps the dialog open and says why", async () => {
    vi.spyOn(api, "listEngagements").mockResolvedValue({ count: 1, engagements: [ACME] });
    vi.spyOn(api, "deleteEngagement").mockRejectedValue(new api.ApiError(500, "boom"));
    renderSwitcher();
    await userEvent.click(await screen.findByRole("button", { name: /all engagements/i }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete engagement Acme Q3" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete engagement" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("a stored selection on an engagement that no longer exists falls back to All", async () => {
    localStorage.setItem(ENGAGEMENT_STORAGE_KEY, "eng-gone");
    vi.spyOn(api, "listEngagements").mockResolvedValue({ count: 1, engagements: [OTHER] });
    renderSwitcher();
    await waitFor(() => expect(localStorage.getItem(ENGAGEMENT_STORAGE_KEY)).toBeNull());
  });

  it("a failed reload after a good one keeps the selection", async () => {
    localStorage.setItem(ENGAGEMENT_STORAGE_KEY, ACME.engagement_id);
    vi.spyOn(api, "listEngagements")
      .mockResolvedValueOnce({ count: 1, engagements: [ACME] })
      .mockRejectedValue(new Error("offline"));
    vi.spyOn(api, "deleteEngagement").mockRejectedValue(new api.ApiError(404, "engagement not found"));
    renderSwitcher();
    // A 404 delete (already gone elsewhere) triggers a reload, which here fails.
    await userEvent.click(await screen.findByRole("button", { name: /acme q3/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete engagement Acme Q3" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete engagement" }));
    await waitFor(() => expect(api.listEngagements).toHaveBeenCalledTimes(2));
    expect(localStorage.getItem(ENGAGEMENT_STORAGE_KEY)).toBe(ACME.engagement_id);
  });

  it("a failed list load keeps the selection (it says nothing about existence)", async () => {
    localStorage.setItem(ENGAGEMENT_STORAGE_KEY, ACME.engagement_id);
    vi.spyOn(api, "listEngagements").mockRejectedValue(new Error("offline"));
    renderSwitcher();
    await screen.findByRole("button", { name: /all engagements/i });
    expect(localStorage.getItem(ENGAGEMENT_STORAGE_KEY)).toBe(ACME.engagement_id);
  });
});

describe("detachMessage", () => {
  it("names the count, and never invents one", () => {
    expect(detachMessage(1)).toBe("1 session will move to No engagement; nothing is deleted.");
    expect(detachMessage(3)).toBe("3 sessions will move to No engagement; nothing is deleted.");
    expect(detachMessage(0)).toBe("No sessions are in it; nothing else is deleted.");
    expect(detachMessage(undefined)).toBe("Its sessions will move to No engagement; nothing is deleted.");
  });
});
