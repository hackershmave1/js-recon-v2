import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { RunProgress } from "./RunProgress";
import { RunDataProvider, useRunData } from "./runData";
import { TenantProvider } from "../../tenant/TenantContext";
import * as sse from "../../api/sseClient";
import * as api from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

// The SSE/status/findings engine now lives in RunDataProvider; RunProgress is a pure
// view over it. Mount both together so these assertions still exercise the real guard
// logic end-to-end (the provider drives the pipeline the view renders).
function renderRun(ui: ReactNode, runId = "r") {
  return render(<TenantProvider><RunDataProvider runId={runId}>{ui}</RunDataProvider></TenantProvider>);
}
// Tiny consumers standing in for the state/findings that used to be lifted via
// callbacks; they let a test observe what the provider now holds in context.
function StateProbe() { const { state } = useRunData(); return <div data-testid="state">{state}</div>; }
function CountProbe() { const { findings } = useRunData(); return <div data-testid="count">{findings?.count ?? "none"}</div>; }

describe("RunProgress", () => {
  it("renders streamed events and fetches findings on open", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onOpen?.();
      h.onEvent({ id: "1", event: "run.progress", data: '{"stage":"analyze"}' });
    });
    renderRun(<RunProgress />);
    await waitFor(() => expect(screen.getByText("analyzing")).toBeInTheDocument());
    expect(screen.getByText(/50%/)).toBeInTheDocument();
    expect(api.getFindings).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000", "r");
  });

  it("shows an error message when the status/findings fetch fails", async () => {
    vi.spyOn(api, "getStatus").mockRejectedValue(new ApiError(404, "run not found"));
    vi.spyOn(api, "getFindings").mockRejectedValue(new ApiError(404, "run not found"));
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    renderRun(<RunProgress />);
    await waitFor(() => expect(screen.getByText(/run not found/i)).toBeInTheDocument());
  });

  it("shows a DONE badge for a fully completed run", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "done", stage: null, done: 2, total: 2, pct: 100, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    renderRun(<RunProgress />);
    const badge = await screen.findByText("DONE");
    expect(badge.className).toContain("chip-done");
  });

  it("shows a distinct PARTIAL badge (Slice Y) when the run finishes incompletely", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "partial", stage: null, done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    renderRun(<RunProgress />);
    const badge = await screen.findByText("PARTIAL");
    expect(badge.className).toContain("chip-partial");
    expect(badge.className).not.toContain("chip-done");
  });

  it("lifts the run state into context so pages can read it", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "done", stage: null, done: 2, total: 2, pct: 100, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    renderRun(<StateProbe />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("done"));
  });

  it("shows run controls (Pause) for an active run", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    renderRun(<RunProgress />);
    expect(await screen.findByRole("button", { name: /pause/i })).toBeInTheDocument();
  });

  it("reflects a live pause request from an SSE signal (A2)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onEvent({ id: "1", event: "run.transition", data: '{"to":"analyzing"}' });
      h.onEvent({ id: "2", event: "run.pause_requested", data: "{}" });
    });
    renderRun(<RunProgress />);
    const btn = await screen.findByRole("button", { name: /pausing/i });
    expect(btn).toBeDisabled();
  });

  it("reflects a live transition to paused from an SSE event (A2)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyze", done: 1, total: 2, pct: 50, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onEvent({ id: "1", event: "run.transition", data: '{"to":"paused"}' });
    });
    renderRun(<RunProgress />);
    expect(await screen.findByRole("button", { name: /resume/i })).toBeInTheDocument();
  });

  it("keeps a live 'discovering' when a late QUEUED status snapshot resolves after it (Bug 1)", async () => {
    // The initial getStatus() is dispatched at onOpen while the run is still QUEUED,
    // but resolves AFTER the live run.transition→discovering. The monotonic guard
    // must reject that stale regression instead of snapping the badge back to Queued.
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "queued", stage: null, done: 0, total: 0, pct: 0, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onOpen?.();                                          // dispatches refresh() -> stale "queued"
      h.onEvent({ id: "1", event: "run.transition", data: '{"from":"queued","to":"discovering"}' });
    });
    renderRun(<RunProgress />);
    await waitFor(() => expect(api.getFindings).toHaveBeenCalled());  // refresh() fully ran (incl. the ignored stale state)
    expect(screen.getByText("discovering")).toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
    expect(screen.getByText("Discover").closest("li")!.className).toContain("is-active");
  });

  it("advances through live stage transitions (Bug 1 regression)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "queued", stage: null, done: 0, total: 0, pct: null, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      for (const to of ["discovering", "fetching", "ingesting", "analyzing"]) {
        h.onEvent({ id: to, event: "run.transition", data: JSON.stringify({ to }) });
      }
    });
    renderRun(<RunProgress />);
    await waitFor(() => expect(screen.getByText("analyzing")).toBeInTheDocument());
    expect(screen.getByText("Analyze").closest("li")!.className).toContain("is-active");
    expect(screen.getByText("Discover").closest("li")!.className).toContain("is-complete");
  });

  it("does not let a late active snapshot revive a terminal PARTIAL (Bug 1)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "correlating", stage: "correlating", done: 3, total: 4, pct: 75, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onEvent({ id: "1", event: "run.transition", data: '{"to":"partial"}' });
      h.onOpen?.();                                          // late refresh reads a stale "correlating"
    });
    renderRun(<RunProgress />);
    await waitFor(() => expect(api.getFindings).toHaveBeenCalled());
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
    expect(screen.queryByText("correlating")).not.toBeInTheDocument();
  });

  it("shows where a PARTIAL run stopped instead of a blank pipeline (Bug 2)", async () => {
    // stage is nulled on the backend at terminal for get_status callers, so the
    // panel pins the last ACTIVE stage from the transition stream and feeds it to
    // RunPipeline -> "Stopped in Correlate", not "ended before completing".
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "queued", stage: null, done: 0, total: 0, pct: null, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      for (const to of ["discovering", "fetching", "ingesting", "analyzing", "correlating"]) {
        h.onEvent({ id: to, event: "run.transition", data: JSON.stringify({ to }) });
      }
      h.onEvent({ id: "end", event: "run.transition", data: '{"to":"partial"}' });
    });
    renderRun(<RunProgress />);
    const badge = await screen.findByText("PARTIAL");
    expect(badge.className).toContain("chip-partial");
    expect(screen.getByText(/Stopped in/)).toBeInTheDocument();
    expect(screen.getByText(/4 of 5 stages completed/)).toBeInTheDocument();
    expect(screen.queryByText(/ended before completing/i)).not.toBeInTheDocument();
  });

  it("preserves the stage when a running crawl is paused mid-stage (Bug 2)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "queued", stage: null, done: 0, total: 0, pct: null, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      for (const to of ["discovering", "fetching", "ingesting"]) {
        h.onEvent({ id: to, event: "run.transition", data: JSON.stringify({ to }) });
      }
      h.onEvent({ id: "p", event: "run.transition", data: '{"to":"paused"}' });
    });
    renderRun(<RunProgress />);
    await screen.findByRole("button", { name: /resume/i });
    expect(screen.getByText("Ingest", { selector: ".rp-step-label" }).closest("li")!.className).toContain("is-paused");
    expect(screen.getByText("Discover").closest("li")!.className).toContain("is-complete");
  });

  it("drives the live progress bar from job.progress events (Bug 3)", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyzing", done: 0, total: 0, pct: null, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onOpen?.();
      h.onEvent({ id: "1", event: "job.progress", data: '{"job_id":"j","done":2,"total":4,"eta_seconds":null}' });
    });
    renderRun(<RunProgress />);
    await waitFor(() => expect(screen.getByText("2 of 4")).toBeInTheDocument());
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("surfaces a blocked crawl's fetch outcome with the dominant reason", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "partial", stage: "correlating", done: 0, total: 0, pct: null, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getFindings").mockResolvedValue({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] });
    vi.spyOn(api, "getAssets").mockResolvedValue({
      domain: "freedomcare.com", status: "ok",
      assets: [
        { url: "https://freedomcare.com/ok.js", source: "html", fetch_status: "ok", analyze_status: "ok" },
        { url: "https://freedomcare.com/a.js", source: "html", fetch_status: "failed", analyze_status: "pending", fetch_error: "target returned HTTP 403" },
        { url: "https://freedomcare.com/b.js", source: "html", fetch_status: "failed", analyze_status: "pending", fetch_error: "target returned HTTP 403" },
        { url: "https://freedomcare.com/c.js", source: "html", fetch_status: "failed", analyze_status: "pending", fetch_error: "target returned HTTP 403" },
      ],
    });
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
    renderRun(<RunProgress />);
    await waitFor(() => expect(screen.getByText(/4 assets · 1 fetched/)).toBeInTheDocument());
    expect(screen.getByText(/3 failed — target returned HTTP 403/)).toBeInTheDocument();
  });

  it("refetches findings on a live terminal transition so results appear without a reload", async () => {
    // The dashboard reads findings only from refresh(). The live SSE terminal
    // transition previously updated the badge but never refetched, so the panel
    // sat on the empty onOpen snapshot until a manual reload. It must refetch on
    // the terminal transition instead.
    vi.spyOn(api, "getStatus").mockResolvedValue({ run_id: "r", state: "analyzing", stage: "analyzing", done: 0, total: 0, pct: null, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false });
    vi.spyOn(api, "getAssets").mockResolvedValue({ domain: null, status: "pending", assets: [] });
    const getFindings = vi.spyOn(api, "getFindings")
      .mockResolvedValueOnce({ run_id: "r", count: 0, coverage: null, spec: null, findings: [] })   // onOpen: still empty
      .mockResolvedValue({ run_id: "r", count: 3, coverage: null, spec: null, findings: [] });        // terminal: results
    vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => {
      h.onOpen?.();                                                             // initial refresh -> 0 findings
      h.onEvent({ id: "1", event: "run.transition", data: '{"to":"done"}' });  // live terminal, stream then ends
    });
    renderRun(<><RunProgress /><CountProbe /></>);
    await waitFor(() => expect(getFindings).toHaveBeenCalledTimes(2));                     // open + terminal
    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("3"));       // lifted findings updated
  });
});
