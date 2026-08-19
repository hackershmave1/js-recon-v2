import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EngagementFilterProvider } from "./EngagementFilterProvider";
import { EngagementSwitcher } from "./EngagementSwitcher";
import { ENGAGEMENT_STORAGE_KEY } from "./engagementFilter";
import { TenantProvider } from "../../tenant/TenantContext";
import type { Engagement } from "../../api/types";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  localStorage.setItem("recon.tenantId", TENANT);
});

const ENGAGEMENT: Engagement = {
  engagement_id: "eng-1",
  name: "Starbucks Security Review",
  in_scope_domains: ["starbucks.com"],
  out_of_scope_domains: [],
  created_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:00Z",
};

async function pickStarbucks() {
  await userEvent.click(await screen.findByRole("button", { name: /all engagements/i }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /starbucks security review/i }));
}

describe("EngagementFilterProvider", () => {
  it("persists the picked engagement so a run started from any route attaches to it", async () => {
    vi.spyOn(api, "listEngagements").mockResolvedValue({ count: 1, engagements: [ENGAGEMENT] });
    render(
      <TenantProvider>
        <EngagementFilterProvider>
          <EngagementSwitcher />
        </EngagementFilterProvider>
      </TenantProvider>,
    );
    await pickStarbucks();
    // NewRunPanel reads exactly this key on submit, so writing it is the whole fix.
    expect(localStorage.getItem(ENGAGEMENT_STORAGE_KEY)).toBe("eng-1");
    expect(await screen.findByText("Starbucks Security Review")).toBeInTheDocument();
  });

  it("without a provider the switcher is a no-op (the bug this fixes)", async () => {
    vi.spyOn(api, "listEngagements").mockResolvedValue({ count: 1, engagements: [ENGAGEMENT] });
    // The landing route used to render the switcher with no provider -> default no-op
    // context -> a pick never reached localStorage, so New Run couldn't attach it.
    render(
      <TenantProvider>
        <EngagementSwitcher />
      </TenantProvider>,
    );
    await pickStarbucks();
    expect(localStorage.getItem(ENGAGEMENT_STORAGE_KEY)).toBeNull();
  });
});
