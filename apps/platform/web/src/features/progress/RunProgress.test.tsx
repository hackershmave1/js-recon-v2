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
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
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

  it("shows a DONE badge for a fully completed run", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "done", stage: null, done: 2, total: 2, pct: 100, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    const badge = await screen.findByText("DONE");
    expect(badge.className).toContain("chip-done");
  });

  it("shows a distinct PARTIAL badge (Slice Y) when the run finishes incompletely", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "partial", stage: null, done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    const badge = await screen.findByText("PARTIAL");
    expect(badge.className).toContain("chip-partial");
    expect(badge.className).not.toContain("chip-done");
  });

  it("lifts the run state to onState when status resolves", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "done", stage: null, done: 2, total: 2, pct: 100, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    const onState = vi.fn();
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} onState={onState} /></TenantProvider>);
    await waitFor(() => expect(onState).toHaveBeenCalledWith("done"));
  });

  it("shows run controls (Pause) for an active run", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    expect(await screen.findByRole("button", { name: /pause/i })).toBeInTheDocument();
  });

  it("reflects a live pause request from an SSE signal (A2)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onEvent({ id: "1", event: "run.transition", data: '{"to":"analyzing"}' });
      h.onEvent({ id: "2", event: "run.pause_requested", data: "{}" });
    });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    const btn = await screen.findByRole("button", { name: /pausing/i });
    expect(btn).toBeDisabled();
  });

  it("reflects a live transition to paused from an SSE event (A2)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onEvent({ id: "1", event: "run.transition", data: '{"to":"paused"}' });
    });
    render(<TenantProvider><RunProgress runId="r" onFindings={() => {}} /></TenantProvider>);
    expect(await screen.findByRole("button", { name: /resume/i })).toBeInTheDocument();
  });
});
