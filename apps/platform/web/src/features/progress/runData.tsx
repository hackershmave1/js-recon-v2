import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { streamRunEvents, type SseEvent } from "../../api/sseClient";
import { getAssets, getFindings, getStatus, ApiError } from "../../api/apiClient";
import { TERMINAL_STATES, type AssetsManifest, type FindingsResponse, type RunControlResult } from "../../api/types";

// A run's active state equals its stage and the state machine only advances
// (recon.domain / runs.state_machine), so each active state has a pipeline rank.
// State reaches this hook from two unordered sources — the live SSE run.transition
// stream and a getStatus() snapshot fetched on (re)connect — and the snapshot can
// resolve late (read while QUEUED, landing after the live "discovering"). These
// guards stop a stale snapshot from regressing the shown state or re-zeroing the
// live progress bar.
const STATE_RANK: Record<string, number> = {
  queued: 0, discovering: 1, fetching: 2, ingesting: 3, analyzing: 4, correlating: 5,
};
const ACTIVE_STAGES = new Set(["discovering", "fetching", "ingesting", "analyzing", "correlating"]);

function supersedes(next: string, current: string): boolean {
  if (current === "…") return true;                                 // initial seed
  if (TERMINAL_STATES.has(current)) return false;                   // terminal is absorbing
  if (TERMINAL_STATES.has(next) || next === "paused") return true;  // must always surface
  if (current === "paused") return true;                            // resume -> active stage
  const rn = STATE_RANK[next];
  const rc = STATE_RANK[current];
  if (rn === undefined || rc === undefined) return true;            // never block the unmodelled
  return rn >= rc;                                                  // never regress the pipeline
}

// A snapshot's numbers/flags are trustworthy only if its state isn't a stale
// regression behind what we already show (a late QUEUED snapshot carries 0/0).
function isStaleSnapshot(snapState: string, current: string): boolean {
  if (current === "…") return false;
  if (TERMINAL_STATES.has(current) && !TERMINAL_STATES.has(snapState)) return true;
  const rc = STATE_RANK[current];
  const rs = STATE_RANK[snapState];
  return rc !== undefined && rs !== undefined && rs < rc;
}

// The whole run's live state, produced once at the run-workspace layout and shared
// with every page (overview pipeline, sources/findings badges, api-spec) via context
// so navigating between pages never tears down the SSE stream or refetches findings.
export interface RunData {
  runId: string;
  state: string;
  stage: string | null;
  pct: number | null;
  done: number;
  total: number;
  eta: number | null;
  error: string | null;
  assets: AssetsManifest | null;
  events: SseEvent[];
  findings: FindingsResponse | null;
  loaded: boolean;
  pauseRequested: boolean;
  cancelRequested: boolean;
  handleControlResult: (res: RunControlResult) => void;
}

const RunDataContext = createContext<RunData | null>(null);

// Every page under the run layout is inside the provider, so this is non-null there;
// it throws for a component rendered outside a run (a wiring bug, not a runtime state).
export function useRunData(): RunData {
  const ctx = useContext(RunDataContext);
  if (!ctx) throw new Error("useRunData must be used within a RunDataProvider");
  return ctx;
}

// Like useRunData but returns null outside a provider, so shell chrome (the sidebar's
// current-run card) can read the run stream in run mode and render nothing in sessions
// mode — where the same Shell renders with no provider around it.
export function useRunDataOptional(): RunData | null {
  return useContext(RunDataContext);
}

function useRunStream(runId: string): RunData {
  const { tenantId } = useTenant();
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [state, setState] = useState<string>("…");
  const [stage, setStage] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [done, setDone] = useState(0);
  const [total, setTotal] = useState(0);
  const [eta, setEta] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assets, setAssets] = useState<AssetsManifest | null>(null);
  const [findings, setFindings] = useState<FindingsResponse | null>(null);
  // Flips true once the first status/findings fetch resolves (success or error), so a
  // page deep-linked/refreshed on a finished run shows a neutral "loading" state
  // instead of briefly flashing "not ready" before the onOpen refresh lands.
  const [loaded, setLoaded] = useState(false);
  const [pauseRequested, setPauseRequested] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  // stateRef mirrors `state` at accept-time so the guard compares against the
  // running value even within a synchronous burst of replayed SSE events (where
  // `state` hasn't re-rendered yet). liveProgressRef flips once job.progress has
  // driven the bar, after which a late snapshot must not re-zero the numbers.
  const stateRef = useRef<string>("…");
  const liveProgressRef = useRef(false);
  // Every state write goes through the monotonic guard. stateRef is updated at
  // accept-time (not via a render-time sync) so ordering holds inside a replay
  // burst. `stage` is pinned to the last ACTIVE stage (state value == stage key)
  // so a terminal/paused render still shows where the run stopped. Returns whether
  // the write was accepted.
  const applyState = useCallback((s: string): boolean => {
    if (!supersedes(s, stateRef.current)) return false;
    stateRef.current = s;
    setState(s);
    if (ACTIVE_STAGES.has(s)) setStage(s);
    return true;
  }, []);
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
        const prev = stateRef.current;
        applyState(s.state);
        // Apply the snapshot's stage/flags/numbers only when it isn't a stale
        // regression (a late QUEUED snapshot would otherwise regress `stage` or
        // re-zero the bar); leave the numbers to the live stream once job.progress
        // has taken over (Bug 1 / Bug 3).
        if (!isStaleSnapshot(s.state, prev)) {
          if (s.stage) setStage(s.stage); // never overwrite the pinned stage with a terminal null
          setPauseRequested(s.pause_requested); setCancelRequested(s.cancel_requested);
          if (!liveProgressRef.current) {
            setPct(s.pct); setDone(s.done); setTotal(s.total); setEta(s.eta_seconds);
          }
        }
        setFindings(f);
        setLoaded(true);
        // The assets manifest powers the crawl fetch-outcome line; it is secondary,
        // so a manifest error must never break the status/findings panel.
        try {
          const manifest = await getAssets(tenantId, runId);
          if (!controller.signal.aborted) setAssets(manifest);
        } catch { /* ignore — manifest is best-effort */ }
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : "Failed to load run");
        setLoaded(true);
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
        else if (e.event === "job.progress") {
          // Live per-stage progress. Once it arrives the stream owns the numbers,
          // so a late getStatus snapshot must not re-zero them (see refresh()).
          try {
            const p = JSON.parse(e.data) as { done?: number; total?: number; eta_seconds?: number | null };
            const d = typeof p.done === "number" ? p.done : 0;
            const t = typeof p.total === "number" ? p.total : 0;
            liveProgressRef.current = true;
            setDone(d); setTotal(t);
            setPct(t > 0 ? Math.round((d / t) * 100) : null);
            setEta(typeof p.eta_seconds === "number" ? p.eta_seconds : null);
          } catch { /* non-JSON payload */ }
        }
        else if (e.event === "run.transition") {
          try {
            const to = (JSON.parse(e.data) as { to?: unknown }).to;
            // Clear the pending-pause hint on an accepted transition. Most transitions
            // resolve it (effected → paused, resumed, or finished). A stage-advance
            // while a pause is still pending clears it momentarily, but the worker
            // honors the pause on its next instruction and the following
            // run.transition{to:paused} restores it. State precedence in RunControls
            // keeps the paused case right. NOTE: a stale getStatus snapshot read while
            // active can still momentarily overwrite a live pause via the paused→active
            // guard rule; it re-syncs on the next event/poll/reconnect (for a paused-
            // then-idle run, worst case the ~300s SSE stream cap) and is not worth
            // blocking (that would also block resume-via-POST).
            if (typeof to === "string") {
              const wasState = stateRef.current;
              if (applyState(to)) {
                setPauseRequested(false);
                // New stage starting: clear the prior stage's job numbers so the bar
                // doesn't flash the old "N of N" before the new job.progress lands.
                if (ACTIVE_STAGES.has(to) && to !== wasState) {
                  setDone(0); setTotal(0); setPct(null); setEta(null);
                }
                // A live terminal transition ENDS the SSE stream (sseClient returns on
                // the terminal fast-path before checkTerminal), so the only findings
                // fetch so far is the onOpen one from when the run was still active and
                // empty. Refetch here, or the dashboard shows DONE over stale (empty)
                // findings/coverage/sources until a manual reload. Idempotent: the
                // monotonic guard rejects a replayed terminal, so applyState is already
                // false above and this never double-fires.
                if (TERMINAL_STATES.has(to)) void refresh();
              }
            }
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
  }, [tenantId, runId, applyState]);

  return {
    runId, state, stage, pct, done, total, eta, error, assets, events, findings, loaded,
    pauseRequested, cancelRequested, handleControlResult,
  };
}

// Mount once at the run layout (keyed by run id) so the stream engine survives page
// navigation between a run's pages but restarts cleanly on a run switch — the
// monotonic-guard refs must not bleed across runs.
export function RunDataProvider({ runId, children }: { runId: string; children: ReactNode }) {
  const value = useRunStream(runId);
  return <RunDataContext.Provider value={value}>{children}</RunDataContext.Provider>;
}
