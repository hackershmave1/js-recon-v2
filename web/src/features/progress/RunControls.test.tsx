import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RunControls } from "./RunControls";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui(state: string, onStateChange = vi.fn()) {
  render(<TenantProvider><RunControls runId="r" state={state} onStateChange={onStateChange} /></TenantProvider>);
  return onStateChange;
}

describe("RunControls", () => {
  it("renders nothing for a terminal run", () => {
    const { container } = render(<TenantProvider><RunControls runId="r" state="done" onStateChange={() => {}} /></TenantProvider>);
    expect(container.querySelector("button")).toBeNull();
  });

  it("shows Pause + Cancel for an active run and pauses", async () => {
    vi.spyOn(api, "pauseRun").mockResolvedValue({ run_id: "r", state: "paused", pause_requested: true });
    const onStateChange = ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(api.pauseRun).toHaveBeenCalledWith(TENANT, "r");
    expect(onStateChange).toHaveBeenCalledWith("paused");
  });

  it("shows Resume (not Pause) for a paused run", () => {
    ui("paused");
    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^pause$/i })).not.toBeInTheDocument();
  });

  it("confirms before cancelling and lifts the new state", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(api, "cancelRun").mockResolvedValue({ run_id: "r", state: "cancelled", cancel_requested: true });
    const onStateChange = ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(window.confirm).toHaveBeenCalled();
    expect(onStateChange).toHaveBeenCalledWith("cancelled");
  });

  it("does not cancel when the confirm is dismissed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const cancel = vi.spyOn(api, "cancelRun");
    ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(cancel).not.toHaveBeenCalled();
  });
});
