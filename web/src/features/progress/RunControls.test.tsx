import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RunControls } from "./RunControls";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { RunControlResult } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui(
  state: string,
  opts: { pauseRequested?: boolean; cancelRequested?: boolean } = {},
  onControlResult = vi.fn(),
) {
  render(
    <TenantProvider>
      <RunControls
        runId="r"
        state={state}
        pauseRequested={opts.pauseRequested ?? false}
        cancelRequested={opts.cancelRequested ?? false}
        onControlResult={onControlResult}
      />
    </TenantProvider>,
  );
  return onControlResult;
}

describe("RunControls", () => {
  it("renders nothing for a terminal run", () => {
    const { container } = render(
      <TenantProvider>
        <RunControls runId="r" state="done" pauseRequested={false} cancelRequested={false} onControlResult={() => {}} />
      </TenantProvider>,
    );
    expect(container.querySelector("button")).toBeNull();
  });

  it("shows Pause + Cancel for an active run and lifts the control result", async () => {
    const res = { run_id: "r", state: "analyzing", pause_requested: true };
    vi.spyOn(api, "pauseRun").mockResolvedValue(res);
    const onControlResult = ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    expect(api.pauseRun).toHaveBeenCalledWith(TENANT, "r");
    expect(onControlResult).toHaveBeenCalledWith(res);
  });

  it("shows Resume (not Pause) for a paused run", () => {
    ui("paused");
    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^pause$/i })).not.toBeInTheDocument();
  });

  it("confirms before cancelling and lifts the new state", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const res = { run_id: "r", state: "cancelled", cancel_requested: true };
    vi.spyOn(api, "cancelRun").mockResolvedValue(res);
    const onControlResult = ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(window.confirm).toHaveBeenCalled();
    expect(onControlResult).toHaveBeenCalledWith(res);
  });

  it("does not cancel when the confirm is dismissed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const cancel = vi.spyOn(api, "cancelRun");
    ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(cancel).not.toHaveBeenCalled();
  });

  // A1: a pause requested but not yet effected must read as pending after a reload,
  // driven purely by the pause_requested prop (seeded from GET /status).
  it("shows a disabled 'Pausing…' with Cancel still available when a pause is pending", () => {
    ui("analyzing", { pauseRequested: true });
    expect(screen.getByRole("button", { name: /pausing/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /^pause$/i })).not.toBeInTheDocument();
  });

  it("shows 'Cancelling…' and no controls once a cancel is pending", () => {
    ui("analyzing", { cancelRequested: true });
    expect(screen.getByText(/cancelling/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("surfaces the server detail when a control action fails", async () => {
    vi.spyOn(api, "pauseRun").mockRejectedValue(new api.ApiError(409, "cannot pause a terminal run"));
    ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    expect(await screen.findByText(/cannot pause a terminal run/i)).toBeInTheDocument();
  });

  it("disables controls while an action is in flight", async () => {
    let resolve!: (v: RunControlResult) => void;
    vi.spyOn(api, "pauseRun").mockReturnValue(new Promise<RunControlResult>((r) => { resolve = r; }));
    ui("analyzing");
    await userEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /cancel/i })).toBeDisabled());
    resolve({ run_id: "r", state: "paused", pause_requested: true });
  });
});
