import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { RunProgress } from "./RunProgress";
import { TenantProvider } from "../../tenant/TenantContext";
import * as sse from "../../api/sseClient";
import * as api from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

describe("RunProgress", () => {
  it("renders streamed events and fetches findings on open", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onOpen?.();
      h.onEvent({ id: "1", event: "run.progress", data: '{"stage":"analyze"}' });
    });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    await waitFor(() => expect(screen.getByText("analyzing")).toBeInTheDocument());
    expect(screen.getByText(/50%/)).toBeInTheDocument();
    expect(api.getFindings).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000", "r");
  });

  it("shows an error message when the status/findings fetch fails", async () => {
    vi.spyOn(api, "getStatus").mockRejectedValue(new ApiError(404, "run not found"));
    vi.spyOn(api, "getFindings").mockRejectedValue(new ApiError(404, "run not found"));
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    await waitFor(() => expect(screen.getByText(/run not found/i)).toBeInTheDocument());
  });
});
