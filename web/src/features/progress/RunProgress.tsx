import { useCallback, useEffect, useRef, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { streamRunEvents, type SseEvent } from "../../api/sseClient";
import { getFindings, getStatus, ApiError } from "../../api/apiClient";
import { TERMINAL_STATES, type FindingsResponse, type RunControlResult } from "../../api/types";
import { RunControls } from "./RunControls";

export function RunProgress(
  { runId, onFindings, onState }: { runId: string; onFindings: (f: FindingsResponse) => void; onState?: (state: string) => void },
) {
  const { tenantId } = useTenant();
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [state, setState] = useState<string>("…");
  const [stage, setStage] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pauseRequested, setPauseRequested] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const onFindingsRef = useRef(onFindings);
  onFindingsRef.current = onFindings;
  const onStateRef = useRef(onState);
  onStateRef.current = onState;
  const applyState = useCallback((s: string) => { setState(s); onStateRef.current?.(s); }, []);
  // A control POST returns the authoritative state + flags; lift them so gating
  // reflects the action immediately, without waiting for the SSE round-trip.
  const handleControlResult = useCallback((res: RunControlResult) => {
    applyState(res.state);
    setPauseRequested(!!res.pause_requested);
    setCancelRequested(!!res.cancel_requested);
  }, [applyState]);

  useEffect(() => {
    if (!tenantId) return;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const [s, f] = await Promise.all([getStatus(tenantId, runId), getFindings(tenantId, runId)]);
        if (controller.signal.aborted) return;
        applyState(s.state); setStage(s.stage); setPct(s.pct);
        setPauseRequested(s.pause_requested); setCancelRequested(s.cancel_requested);
        onFindingsRef.current(f);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : "Failed to load run");
      }
    };
    streamRunEvents(runId, tenantId, {
      signal: controller.signal,
      onOpen: () => { void refresh(); },
      onEvent: (e) => {
        setEvents((prev) => [...prev, e]);
        // Reflect control intent + non-terminal state live (A2): SSE carries named
        // signals for a requested pause/cancel, and run.transition carries the new
        // state, so gating updates without waiting for a poll or a reconnect.
        if (e.event === "run.pause_requested") setPauseRequested(true);
        else if (e.event === "run.cancel_requested") setCancelRequested(true);
        else if (e.event === "run.transition") {
          try {
            const to = (JSON.parse(e.data) as { to?: unknown }).to;
            // Clear the pending-pause hint on any transition. Most transitions
            // resolve it (effected → paused, resumed, or finished). A stage-advance
            // while a pause is still pending clears it momentarily, but the worker
            // honors the pause on its next instruction and the following
            // run.transition{to:paused} restores it (usually coalesced into one
            // render). State precedence in RunControls keeps the paused case right.
            if (typeof to === "string") { applyState(to); setPauseRequested(false); }
          } catch { /* non-JSON payload */ }
        }
      },
      checkTerminal: async () => {
        try {
          const s = await getStatus(tenantId, runId);
          if (controller.signal.aborted) return true;
          applyState(s.state);
          setPauseRequested(s.pause_requested); setCancelRequested(s.cancel_requested);
          if (TERMINAL_STATES.has(s.state)) { void refresh(); return true; }
          return false;
        } catch (e) {
          if (controller.signal.aborted) return true;
          setError(e instanceof ApiError ? e.message : "Failed to load run");
          return true;
        }
      },
      onFallback: () => { void refresh(); },
    });
    return () => controller.abort();
  }, [tenantId, runId]);

  return (
    <div className="card">
      <h2>Run {runId}</h2>
      <p>
        State: <strong>{state}</strong>
        {/* Slice Y: done vs partial are both terminal but not the same outcome —
            a distinct chip per terminal state (not just the plain word) keeps a
            partially-completed crawl from reading as a clean success at a glance. */}
        {TERMINAL_STATES.has(state) && (
          <span className={`chip chip-${state}`}>{state.toUpperCase()}</span>
        )}
        {stage ? ` · ${stage}` : ""}{pct != null ? ` · ${pct}%` : ""}
      </p>
      {state !== "…" && (
        <RunControls
          runId={runId}
          state={state}
          pauseRequested={pauseRequested}
          cancelRequested={cancelRequested}
          onControlResult={handleControlResult}
        />
      )}
      {error && <p className="sev-high">{error}</p>}
      <ul>{events.map((e, i) => <li key={i} className="muted">{e.event}: {e.data}</li>)}</ul>
    </div>
  );
}
