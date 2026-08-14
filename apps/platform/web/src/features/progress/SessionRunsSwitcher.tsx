import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { getSessionRuns } from "../../api/apiClient";
import type { SessionRunRef } from "../../api/types";
import { relativeTime } from "../../shell/relativeTime";
import "./sessionRunsSwitcher.css";

// Capture rounds under one session become SEPARATE runs (analyze seals a run; the next
// capture opens a fresh one), and the app otherwise surfaces only a session's latest run —
// so an earlier round "disappeared" for operators after they captured again. This lists every
// run in the current run's session, so any round is one click away. It renders nothing for a
// single-run session (no clutter) or before the status snapshot carries a session id.
export function SessionRunsSwitcher({ runId, sessionId }: { runId: string; sessionId: string | null }) {
  const { tenantId } = useTenant();
  const [runs, setRuns] = useState<SessionRunRef[] | null>(null);

  useEffect(() => {
    if (!tenantId || !sessionId) { setRuns(null); return; }
    let live = true;
    getSessionRuns(tenantId, sessionId)
      .then((r) => { if (live) setRuns(r.runs); })
      .catch(() => { if (live) setRuns(null); });
    return () => { live = false; };
  }, [tenantId, sessionId]);

  if (!runs || runs.length <= 1) return null;

  return (
    <nav className="srs" aria-label="Runs in this session">
      <span className="srs-label">{runs.length} runs in this session</span>
      <ul className="srs-list">
        {runs.map((run) => {
          const current = run.run_id === runId;
          const when = run.ended_at ?? run.started_at ?? run.created_at;
          const label = `${run.run_id.slice(0, 8)} · ${run.state}`
            + (when ? ` · ${relativeTime(when)}` : "")
            + (run.target ? ` · ${run.target}` : "");
          return (
            <li key={run.run_id}>
              {current
                ? <span className="srs-run is-current" aria-current="page">{label} · this run</span>
                : <Link className="srs-run" to={`/runs/${run.run_id}`}>{label}</Link>}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
