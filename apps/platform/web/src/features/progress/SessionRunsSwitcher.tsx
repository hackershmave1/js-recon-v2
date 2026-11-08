import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { getSessionRuns } from "../../api/apiClient";
import { TERMINAL_STATES, type SessionRunRef } from "../../api/types";
import { relativeTime } from "../../shell/relativeTime";
import "./sessionRunsSwitcher.css";

// Every capture of a session (one run each) is listed with its endpoint count and a bar against
// the session's LARGEST capture, so a big earlier capture stays visible when a later one comes
// back small. The run ID is never shown; the square button copies it. Renders nothing for a
// single-run session (no clutter) or before the status snapshot carries a session id.
// `runState` is the current run's LIVE state: the list refetches when that run comes to rest or
// finishes (its counts changed), and its own row shows the live state, not the fetched one.
export function SessionRunsSwitcher({ runId, sessionId, runState }: {
  runId: string; sessionId: string | null; runState?: string;
}) {
  const { tenantId } = useTenant();
  const [runs, setRuns] = useState<SessionRunRef[] | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const clearCopied = useRef<number | undefined>(undefined);
  const settled = runState === "capturing" || (!!runState && TERMINAL_STATES.has(runState));

  useEffect(() => {
    if (!tenantId || !sessionId) { setRuns(null); return; }
    let live = true;
    getSessionRuns(tenantId, sessionId)
      .then((r) => { if (live) setRuns(r.runs); })
      .catch(() => { if (live) setRuns(null); });
    return () => { live = false; };
    // runState only while settled: each pass ending (or the finish) refetches; the active
    // stages in between don't.
  }, [tenantId, sessionId, settled ? runState : null]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => window.clearTimeout(clearCopied.current), []);

  if (!runs || runs.length <= 1) return null;
  const largest = Math.max(0, ...runs.map((r) => r.endpoints ?? 0));

  const say = (message: string) => {
    setCopied(message);
    window.clearTimeout(clearCopied.current);
    clearCopied.current = window.setTimeout(() => setCopied(null), 4000);
  };
  const copy = (id: string) => {
    // Called inside the click, as the clipboard API requires. A refusal (or no clipboard on an
    // insecure origin) shows the ID itself, so it can still be copied by hand.
    Promise.resolve()
      .then(() => navigator.clipboard.writeText(id))
      .then(() => say("Run ID copied"), () => say(`Couldn't copy — run ID: ${id}`));
  };

  return (
    <nav className="srs" aria-label="Runs in this session">
      <span className="srs-label">{runs.length} runs in this session</span>
      <ul className="srs-list">
        {runs.map((run, index) => {
          const current = run.run_id === runId;
          const state = current && runState ? runState : run.state;
          const name = `Capture ${runs.length - index}`;  // newest first: unique within the session
          const when = run.ended_at ?? run.started_at ?? run.created_at;
          const label = `${name} · ${when ? relativeTime(when) : "—"}${run.target ? ` · ${run.target}` : ""}`;
          const endpoints = run.endpoints ?? null;
          return (
            <li key={run.run_id} className={"srs-row" + (current ? " is-current" : "")}>
              {current
                ? <span className="srs-when" aria-current="page" title={label}>this run · {label}</span>
                : <Link className="srs-when" to={`/runs/${run.run_id}`} title={label}>{label}</Link>}
              {state === "capturing" && <span className="srs-live">capturing</span>}
              <span className="srs-count">{endpoints === null ? "—" : endpoints} endpoints</span>
              <button type="button" className="srs-copy" aria-label={`Copy run ID (${name})`}
                title="Copy run ID" onClick={() => copy(run.run_id)} />
              {endpoints !== null && largest > 0 && (
                <span className="srs-bar" role="meter" aria-label={`${name}: ${endpoints} of ${largest} endpoints`}
                  aria-valuemin={0} aria-valuemax={largest} aria-valuenow={endpoints}>
                  <i style={{ width: `${(endpoints / largest) * 100}%` }} />
                </span>
              )}
            </li>
          );
        })}
      </ul>
      <span className="srs-copied" role="status" aria-live="polite">{copied ?? ""}</span>
    </nav>
  );
}
