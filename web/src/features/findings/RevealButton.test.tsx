import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RevealButton } from "./RevealButton";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });
const ui = () => render(<TenantProvider><RevealButton runId="r" hash="h" /></TenantProvider>);

describe("RevealButton", () => {
  it("shows the value once and disables the button", async () => {
    vi.spyOn(api, "revealSecret").mockResolvedValue({ finding_hash: "h", value: "AKIA-secret" });
    ui();
    await userEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(await screen.findByText("AKIA-secret")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /revealed/i })).toBeDisabled();
  });

  it("maps 409 integrity to a specific message", async () => {
    vi.spyOn(api, "revealSecret").mockRejectedValue(new ApiError(409, "cannot reveal secret: integrity"));
    ui();
    await userEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(await screen.findByText(/no longer matches/i)).toBeInTheDocument();
  });

  it("maps 410 source_gone to a purged message", async () => {
    vi.spyOn(api, "revealSecret").mockRejectedValue(new ApiError(410, "cannot reveal secret: source_gone"));
    ui();
    await userEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(await screen.findByText(/purged/i)).toBeInTheDocument();
  });
});
