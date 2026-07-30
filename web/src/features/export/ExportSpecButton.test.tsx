import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExportSpecButton } from "./ExportSpecButton";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui() { return render(<TenantProvider><ExportSpecButton runId="r" /></TenantProvider>); }

describe("ExportSpecButton", () => {
  it("downloads the spec via a blob anchor on click", async () => {
    vi.spyOn(api, "exportOpenApi").mockResolvedValue(new Blob(["{}"], { type: "application/json" }));
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    ui();
    await userEvent.click(screen.getByRole("button", { name: /export spec/i }));
    expect(api.exportOpenApi).toHaveBeenCalledWith(TENANT, "r", "json");
    expect(clickSpy).toHaveBeenCalled();
  });

  it("exports yaml when the format is switched", async () => {
    vi.spyOn(api, "exportOpenApi").mockResolvedValue(new Blob(["a: 1"]));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    ui();
    await userEvent.selectOptions(screen.getByLabelText(/export format/i), "yaml");
    await userEvent.click(screen.getByRole("button", { name: /export spec/i }));
    expect(api.exportOpenApi).toHaveBeenCalledWith(TENANT, "r", "yaml");
  });

  it("shows an inline error when export fails", async () => {
    vi.spyOn(api, "exportOpenApi").mockRejectedValue(new api.ApiError(500, "failed to build a valid OpenAPI document"));
    ui();
    await userEvent.click(screen.getByRole("button", { name: /export spec/i }));
    expect(await screen.findByText(/couldn't export spec/i)).toBeInTheDocument();
  });
});
