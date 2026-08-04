import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TriageControls } from "./TriageControls";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

describe("TriageControls", () => {
  it("posts the selected status", async () => {
    const spy = vi.spyOn(api, "triageFinding").mockResolvedValue({ finding_hash: "h", status: "confirmed", note: null, actor: null, updated_at: "now" });
    render(<TenantProvider><TriageControls runId="r" hash="h" current="open" /></TenantProvider>);
    await userEvent.selectOptions(screen.getByLabelText(/triage/i), "confirmed");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(spy).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000", "r", "h", { status: "confirmed", note: undefined });
  });
});
