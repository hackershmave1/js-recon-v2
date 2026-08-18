import "./progress.css";

// The real backend pipeline (recon.domain.STAGE_ORDER). During an active run the
// run STATE equals its current STAGE (state_for_stage), and the state machine
// forbids skipping stages — so a stage's status is derivable from state/stage
// alone, with no invented numbers (project rule §5).
const STAGES = [
  { key: "discovering", label: "Discover" },
  { key: "fetching", label: "Fetch" },
  { key: "ingesting", label: "Ingest" },
  { key: "analyzing", label: "Analyze" },
  { key: "correlating", label: "Correlate" },
] as const;

const TERMINAL = new Set(["done", "partial", "failed", "cancelled"]);
type NodeStatus = "complete" | "active" | "paused" | "stopped" | "pending";

function stageIndex(key: string | null): number {
  return STAGES.findIndex((s) => s.key === key);
}

function nodeStatuses(state: string, stage: string | null): NodeStatus[] {
  if (state === "done") return STAGES.map(() => "complete");
  if (state === "queued") return STAGES.map(() => "pending");
  if (state === "paused") {
    const i = stageIndex(stage);
    return STAGES.map((_, k) => (i < 0 ? "pending" : k < i ? "complete" : k === i ? "paused" : "pending"));
  }
  if (TERMINAL.has(state)) {
    // partial / failed / cancelled: stages can't be skipped, so everything before
    // the stage it stopped in completed. Unknown stop stage -> leave neutral.
    const i = stageIndex(stage);
    if (i < 0) return STAGES.map(() => "pending");
    return STAGES.map((_, k) => (k < i ? "complete" : k === i ? "stopped" : "pending"));
  }
  // active: state IS the current stage; prior stages are complete.
  const i = stageIndex(state);
  if (i < 0) return STAGES.map(() => "pending");
  return STAGES.map((_, k) => (k < i ? "complete" : k === i ? "active" : "pending"));
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s ? `${m}m ${s}s` : `${m}m`;
}

function CheckMark() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor"
      strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3.5 8.4l3 3 6-7" />
    </svg>
  );
}

export function RunPipeline(
  { state, stage, pct, done, total, etaSeconds, emptyCrawl = false }: {
    state: string; stage: string | null; pct: number | null;
    done: number; total: number; etaSeconds: number | null; emptyCrawl?: boolean;
  },
) {
  const statuses = nodeStatuses(state, stage);
  const isTerminal = TERMINAL.has(state);
  const active = !isTerminal && state !== "queued";
  // failed / cancelled read as danger; partial (some assets succeeded) as warn.
  const stopTone = state === "partial" ? "is-warn" : "is-fail";
  const currentLabel = STAGES[stageIndex(state)]?.label ?? null;
  const completed = statuses.filter((s) => s === "complete").length;
  const stoppedAt = stageIndex(stage);

  return (
    <div className="rp-pipeline">
      <ol className="rp-steps" aria-label="Recon pipeline progress">
        {STAGES.map((s, i) => {
          const st = statuses[i];
          const cls = `rp-step is-${st}` + (st === "stopped" ? ` ${stopTone}` : "");
          return (
            <li key={s.key} className={cls} aria-current={st === "active" ? "step" : undefined}>
              <span className="rp-step-dot" aria-hidden="true">
                {st === "complete" ? <CheckMark />
                  : st === "active" || st === "paused" ? <span className="rp-dot-inner" />
                  : st === "stopped" ? "!" : null}
              </span>
              <span className="rp-step-label">{s.label}</span>
            </li>
          );
        })}
      </ol>

      {active && (
        <div className="rp-bar-wrap">
          <div className="rp-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={pct ?? undefined} aria-label="Current stage progress">
            <div className={`rp-bar-fill${pct == null ? " is-indeterminate" : ""}`}
              style={pct != null ? { width: `${pct}%` } : undefined} />
          </div>
          <div className="rp-bar-meta">
            <span>{currentLabel ? `${currentLabel}…` : "Working…"}</span>
            <span className="rp-bar-nums">
              {total > 0 && <span>{done} of {total}</span>}
              {pct != null && <span className="rp-mono">{pct}%</span>}
              {etaSeconds != null && etaSeconds > 0 && <span className="rp-mono">~{formatEta(etaSeconds)} left</span>}
            </span>
          </div>
        </div>
      )}

      {state === "done" && !emptyCrawl && (
        <p className="rp-outcome"><b>{STAGES.length} of {STAGES.length}</b> stages complete</p>
      )}
      {state === "done" && emptyCrawl && (
        <p className="rp-outcome rp-outcome-empty">Completed — no in-scope JavaScript found</p>
      )}
      {isTerminal && state !== "done" && (
        <p className="rp-outcome">
          {stoppedAt >= 0
            ? <>Stopped in <b>{STAGES[stoppedAt].label}</b> · {completed} of {STAGES.length} stages completed</>
            : <>Run ended before completing the pipeline</>}
        </p>
      )}
    </div>
  );
}
