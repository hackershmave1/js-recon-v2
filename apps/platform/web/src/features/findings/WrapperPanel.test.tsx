import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WrapperPanel } from "./WrapperPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { WrapperRule } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui() {
  vi.spyOn(api, "listWrapperRules").mockResolvedValue([]);
  return render(<TenantProvider><WrapperPanel runId="r" /></TenantProvider>);
}

const EXISTING: WrapperRule = { id: "w-1", callee: "api", actor: null };

describe("WrapperPanel", () => {
  it("teaches a wrapper and lists it", async () => {
    vi.spyOn(api, "addWrapperRule").mockResolvedValue({
      rule: { id: "1", callee: "api", actor: null }, recovered: 3,
    });
    ui();
    await userEvent.type(screen.getByLabelText(/wrapper callee/i), "api");
    await userEvent.click(screen.getByRole("button", { name: /teach wrapper/i }));
    expect(api.addWrapperRule).toHaveBeenCalledWith(TENANT, "r", { callee: "api" });
    expect(await screen.findByText(/recovered 3 rows/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete wrapper api" })).toBeInTheDocument();
  });

  it("shows a readable message on a 422 invalid callee", async () => {
    vi.spyOn(api, "addWrapperRule").mockRejectedValue(new api.ApiError(422, "invalid callee: 'a.b'"));
    ui();
    await userEvent.type(screen.getByLabelText(/wrapper callee/i), "a.b");
    await userEvent.click(screen.getByRole("button", { name: /teach wrapper/i }));
    expect(await screen.findByText(/invalid callee/i)).toBeInTheDocument();
  });

  it("deletes a wrapper and removes it from the list", async () => {
    vi.spyOn(api, "listWrapperRules").mockResolvedValue([EXISTING]);
    vi.spyOn(api, "deleteWrapperRule").mockResolvedValue(undefined);
    render(<TenantProvider><WrapperPanel runId="r" /></TenantProvider>);
    const del = await screen.findByRole("button", { name: "Delete wrapper api" });
    await userEvent.click(del);
    expect(api.deleteWrapperRule).toHaveBeenCalledWith(TENANT, "r", "w-1");
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Delete wrapper api" })).not.toBeInTheDocument();
    });
  });
});
