import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NewEngagementModal } from "./NewEngagementModal";
import * as api from "../../api/apiClient";

beforeEach(() => vi.restoreAllMocks());

// Walkthrough F06: an engagement has no scope of its own, so the form no longer asks for one.
describe("NewEngagementModal", () => {
  it("asks for a name only, with no scope fields", () => {
    render(<NewEngagementModal tenantId="t1" onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByLabelText("ENGAGEMENT NAME")).toBeInTheDocument();
    expect(screen.queryByText(/IN-SCOPE/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/OUT OF SCOPE/i)).not.toBeInTheDocument();
    expect(document.querySelectorAll("textarea")).toHaveLength(0);
  });

  it("creates the engagement with the name alone", async () => {
    const created = { engagement_id: "e1", name: "Acme Q4 review", in_scope_domains: [], out_of_scope_domains: [], created_at: "t", updated_at: "t" };
    const spy = vi.spyOn(api, "createEngagement").mockResolvedValue(created);
    const onCreated = vi.fn();
    render(<NewEngagementModal tenantId="t1" onClose={() => {}} onCreated={onCreated} />);
    await userEvent.type(screen.getByLabelText("ENGAGEMENT NAME"), "  Acme Q4 review ");
    await userEvent.click(screen.getByRole("button", { name: /create engagement/i }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    expect(spy).toHaveBeenCalledWith("t1", { name: "Acme Q4 review" });
  });

  it("refuses an empty name", async () => {
    render(<NewEngagementModal tenantId="t1" onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByRole("button", { name: /create engagement/i })).toBeDisabled();
  });
});
