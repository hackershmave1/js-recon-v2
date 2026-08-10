import { TERMINAL_STATES, type AssetsManifest } from "../../api/types";
import { useRunData } from "./runData";
import { RunControls } from "./RunControls";
import { RunPipeline } from "./RunPipeline";

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

// Presentational: the run's live pipeline card — state chip + run controls + the
// stage pipeline + a crawl's fetch outcome + the activity log. It reads the shared
// run stream from context (RunDataProvider owns the SSE/fetch engine), so it holds
// no fetching of its own and lives on the Overview page.
export function RunProgress() {
  const {
    runId, state, stage, pct, done, total, eta, error, assets, events,
    pauseRequested, cancelRequested, handleControlResult,
  } = useRunData();
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
