import { useCallback, useEffect, useRef, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { streamRunEvents, type SseEvent } from "../../api/sseClient";
import { getAssets, getFindings, getStatus, ApiError } from "../../api/apiClient";
import { TERMINAL_STATES, type AssetsManifest, type FindingsResponse, type RunControlResult } from "../../api/types";
import { RunControls } from "./RunControls";
import { RunPipeline } from "./RunPipeline";

// A run's active state equals its stage and the state machine only advances
// (recon.domain / runs.state_machine), so each active state has a pipeline rank.
// State reaches this component from two unordered sources — the live SSE
// run.transition stream and a getStatus() snapshot fetched on (re)connect — and
// the snapshot can resolve late (read while QUEUED, landing after the live
// "discovering"). These guards stop a stale snapshot from regressing the shown
// state or re-zeroing the live progress bar.
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

interface FetchSummary { total: number; fetched: number; failed: number; pending: number; reason: string | null; }

// Aggregate a crawl's per-asset fetch outcome so a blocked run (e.g. a Cloudflare
// 403 on every asset) reads as "blocked", not an empty success. Returns null for a
// non-crawl run (no assets). `reason` is the most common failure message.
function summarizeFetch(m: AssetsManifest | null): FetchSummary | null {
  if (!m || m.assets.length === 0) return null;
  let fetched = 0;
  let failed = 0;
  let pending = 0;
  const reasons = new Map<string, number>();
  for (const a of m.assets) {
    if (a.fetch_status === "ok") fetched += 1;
    else if (a.fetch_status === "failed") {
      failed += 1;
      const r = a.fetch_error?.trim();
      if (r) reasons.set(r, (reasons.get(r) ?? 0) + 1);
    } else pending += 1;
  }
  let reason: string | null = null;
  let max = 0;
  for (const [r, n] of reasons) if (n > max) { max = n; reason = r; }
  return { total: m.assets.length, fetched, failed, pending, reason };
}

export function RunProgress(
  { runId, onFindings, onState }: { runId: string; onFindings: (f: FindingsResponse) => void; onState?: (state: string) => void },
) {
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
  const [pauseRequested, setPauseRequested] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  // stateRef mirrors `state` at accept-time so the guard compares against the
  // running value even within a synchronous burst of replayed SSE events (where
  // `state` hasn't re-rendered yet). liveProgressRef flips once job.progress has
  // driven the bar, after which a late snapshot must not re-zero the numbers.
  const stateRef = useRef<string>("…");
  const liveProgressRef = useRef(false);
  const onFindingsRef = useRef(onFindings);
  onFindingsRef.current = onFindings;
  const onStateRef = useRef(onState);
  onStateRef.current = onState;
  // Every state write goes through the monotonic guard. stateRef is updated at
  // accept-time (not via a render-time sync) so ordering holds inside a replay
  // burst. `stage` is pinned to the last ACTIVE stage (state value == stage key)
  // so a terminal/paused render still shows where the run stopped. Returns whether
  // the write was accepted.
  const applyState = useCallback((s: string): boolean => {
    if (!supersedes(s, stateRef.current)) return false;
    stateRef.current = s;
    setState(s);
    onStateRef.current?.(s);
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
        onFindingsRef.current(f);
        // The assets manifest powers the crawl fetch-outcome line; it is secondary,
        // so a manifest error must never break the status/findings panel.
        try {
          const manifest = await getAssets(tenantId, runId);
          if (!controller.signal.aborted) setAssets(manifest);
        } catch { /* ignore — manifest is best-effort */ }
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
  }, [tenantId, runId]);

  const fetchSummary = summarizeFetch(assets);

  return (
    <div className="card">
      <div className="rp-head">
        <h2 className="rp-title">Run <span className="rp-id" title={runId}>{runId.slice(0, 8)}</span></h2>
        <div className="rp-head-right">
          {/* Slice Y: done vs partial are both terminal but not the same outcome —
              a distinct chip per terminal state keeps a partially-completed crawl
              from reading as a clean success. Active/queued/paused show the live
              state word (kept verbatim; capitalised for display only). */}
          {TERMINAL_STATES.has(state)
            ? <span className={`chip chip-${state}`}>{state.toUpperCase()}</span>
            : state !== "…" && <span className="rp-state">{state}</span>}
          {state !== "…" && (
            <RunControls
              runId={runId}
              state={state}
              pauseRequested={pauseRequested}
              cancelRequested={cancelRequested}
              onControlResult={handleControlResult}
            />
          )}
        </div>
      </div>

      {state !== "…" && (
        <RunPipeline state={state} stage={stage} pct={pct} done={done} total={total} etaSeconds={eta} />
      )}

      {fetchSummary && (
        <p className="rp-fetch">
          {fetchSummary.total} assets · {fetchSummary.fetched} fetched
          {fetchSummary.failed > 0 && (
            <span className="rp-fetch-fail">
              {" · "}{fetchSummary.failed} failed{fetchSummary.reason ? ` — ${fetchSummary.reason}` : ""}
            </span>
          )}
          {fetchSummary.pending > 0 && <> · {fetchSummary.pending} pending</>}
        </p>
      )}

      {error && <p className="sev-high">{error}</p>}

      {events.length > 0 && (
        <details className="rp-log">
          <summary className="rp-log-summary">
            Activity log <span className="rp-log-count">{events.length} events</span>
          </summary>
          <ul className="rp-log-list">
            {events.map((e, i) => <li key={i} className="muted">{e.event}: {e.data}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
