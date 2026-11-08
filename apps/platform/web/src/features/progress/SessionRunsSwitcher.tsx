import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { getSessionRuns } from "../../api/apiClient";
import type { SessionRunRef } from "../../api/types";
import { relativeTime } from "../../shell/relativeTime";
import "./sessionRunsSwitcher.css";

// Every capture of a session (one run each) is listed with its endpoint count and a bar against
// the session's LARGEST capture, so a big earlier capture stays visible when a later one comes
// back small. The run ID is never shown; the square button copies it. Renders nothing for a
// single-run session (no clutter) or before the status snapshot carries a session id.
export function SessionRunsSwitcher({ runId, sessionId }: { runId: string; sessionId: string | null }) {
  const { tenantId } = useTenant();
  const [runs, setRuns] = useState<SessionRunRef[] | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId || !sessionId) { setRuns(null); return; }
    let live = true;
    getSessionRuns(tenantId, sessionId)
      .then((r) => { if (live) setRuns(r.runs); })
      .catch(() => { if (live) setRuns(null); });
    return () => { live = false; };
  }, [tenantId, sessionId]);

  if (!runs || runs.length <= 1) return null;
  const largest = Math.max(0, ...runs.map((r) => r.endpoints ?? 0));

  const copy = (id: string) => {
    // Called inside the click, as the clipboard API requires; a refusal says so instead of
    // pretending it copied.
    Promise.resolve()
      .then(() => navigator.clipboard.writeText(id))
      .then(() => setCopied("Run ID copied"), () => setCopied("Couldn't copy — your browser blocked it"));
  };

  return (
    <nav className="srs" aria-label="Runs in this session">
      <span className="srs-label">{runs.length} runs in this session</span>
      <ul className="srs-list">
        {runs.map((run) => {
          const current = run.run_id === runId;
          const when = run.ended_at ?? run.started_at ?? run.created_at;
          const label = (when ? relativeTime(when) : "—") + (run.target ? ` · ${run.target}` : "");
          const endpoints = run.endpoints ?? null;
          return (
            <li key={run.run_id} className={"srs-row" + (current ? " is-current" : "")}>
              {current
                ? <span className="srs-when" aria-current="page">{label} · this run</span>
                : <Link className="srs-when" to={`/runs/${run.run_id}`}>{label}</Link>}
              {run.state === "capturing" && <span className="srs-live">capturing</span>}
              <span className="srs-count">{endpoints === null ? "—" : endpoints} endpoints</span>
              <button type="button" className="srs-copy" aria-label={`Copy run ID (${label})`}
                title="Copy run ID" onClick={() => copy(run.run_id)} />
              {endpoints !== null && (
                <span className="srs-bar" role="meter" aria-label={`${endpoints} of ${largest} endpoints`}
                  aria-valuemin={0} aria-valuemax={largest} aria-valuenow={endpoints}>
                  <i style={{ width: largest ? `${(endpoints / largest) * 100}%` : "0%" }} />
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {copied && <span className="srs-copied" role="status">{copied}</span>}
    </nav>
  );
}
