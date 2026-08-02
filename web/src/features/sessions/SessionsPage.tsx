import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import {
  listSessions, renameSession, archiveSession, deleteSession, rerunSession, ApiError,
} from "../../api/apiClient";
import type { SessionSummary } from "../../api/types";
import { Icon } from "../../shell/icons";
import { useEngagementFilter } from "./engagementFilter";
import "./sessions.css";

// Map a run state to the pill/dot colour class. Colour encodes outcome, matching the
// app's one pill language (styles.css): done = ok, partial/paused = warn, failed = bad,
// an active stage = run (lime, pulsing), queued/cancelled = muted.
const STATUS_CLASS: Record<string, string> = {
  done: "ok", partial: "warn", paused: "warn",
  failed: "bad", cancelled: "muted", queued: "muted",
  running: "run", discovering: "run", fetching: "run",
  ingesting: "run", analyzing: "run", correlating: "run",
};
const ACTIVE_STATES = new Set([
  "running", "discovering", "fetching", "ingesting", "analyzing", "correlating",
]);

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

// A null stat means "no run yet" or "analyze hasn't emitted" — render "—", never a 0
// that reads as a real measurement (project rule §5, honest real data).
const statText = (n: number | null): string => (n == null ? "—" : String(n));

export function SessionsPage({ tenantId }: { tenantId: string }) {
  const navigate = useNavigate();
  const { engagementId } = useEngagementFilter();
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(() => {
    setError(null);
    listSessions(tenantId, { archived: showArchived })
      .then((r) => setSessions(r.sessions))
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load sessions"));
  }, [tenantId, showArchived]);

  useEffect(() => { reload(); }, [reload]);

  // Close an open kebab menu on any outside click.
  useEffect(() => {
    if (!menuFor) return;
    function onDoc(e: MouseEvent) {
      if (!(e.target as HTMLElement).closest(".sx-card-actions")) setMenuFor(null);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuFor]);

  const visible = (sessions ?? []).filter(
    (s) => engagementId === null || s.engagement_id === engagementId,
  );

  async function withBusy(id: string, action: () => Promise<unknown>) {
    setBusyId(id);
    setMenuFor(null);
    try {
      await action();
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed");
    } finally {
      setBusyId(null);
    }
  }

  function onRename(s: SessionSummary) {
    const next = window.prompt("Rename session", s.name ?? s.host);
    if (next && next.trim()) {
      void withBusy(s.session_id, () => renameSession(tenantId, s.session_id, next.trim()));
    }
  }
  function onArchive(s: SessionSummary) {
    void withBusy(s.session_id, () => archiveSession(tenantId, s.session_id, !s.archived));
  }
  function onDelete(s: SessionSummary) {
    if (window.confirm("Delete this session and all its runs? This cannot be undone.")) {
      void withBusy(s.session_id, () => deleteSession(tenantId, s.session_id));
    }
  }
  async function onRerun(s: SessionSummary) {
    setBusyId(s.session_id);
    setMenuFor(null);
    try {
      const run = await rerunSession(tenantId, s.session_id);
      navigate(`/runs/${run.run_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Re-run failed");
      setBusyId(null);
    }
  }
  function open(s: SessionSummary) {
    if (s.latest_run) navigate(`/runs/${s.latest_run.run_id}`);
  }

  return (
    <div className="sx">
      <div className="sx-head">
        <div>
          <h1 className="sx-title">Sessions</h1>
          <div className="sx-sub">Recon targets and their latest runs</div>
        </div>
        <div className="sx-head-actions">
          <label className="sx-arch">
            <input type="checkbox" checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived
          </label>
          <a className="sx-new" href="/"><Icon name="plus" size={14} />New Recon</a>
        </div>
      </div>

      {error && <div className="sx-error">{error}</div>}
      {sessions == null && !error && <div className="sx-empty">Loading sessions…</div>}
      {sessions != null && visible.length === 0 && !error && (
        sessions.length === 0 ? (
          <div className="sx-empty">
            <div className="sx-empty-title">No sessions yet</div>
            <div>Start a recon run to create your first session.</div>
            <a className="sx-new" href="/"><Icon name="plus" size={14} />New Recon</a>
          </div>
        ) : (
          <div className="sx-empty">
            <div className="sx-empty-title">No sessions in this engagement</div>
            <div>Switch engagement in the sidebar, or start a new recon run.</div>
          </div>
        )
      )}

      <div className="sx-grid">
        {visible.map((s) => {
          const state = s.latest_run?.state ?? "queued";
          const cls = STATUS_CLASS[state] ?? "muted";
          const running = ACTIVE_STATES.has(state);
          const lastRun = s.latest_run
            ? s.latest_run.ended_at ?? s.latest_run.started_at ?? s.latest_run.created_at
            : null;
          return (
            <div key={s.session_id}
              className={"sx-card status-" + cls + (busyId === s.session_id ? " is-busy" : "")}>
              <div className="sx-card-body" role="button" tabIndex={0}
                aria-label={`Open ${s.host}`}
                onClick={() => open(s)}
                onKeyDown={(e) => { if (e.key === "Enter") open(s); }}>
                <div className="sx-card-top">
                  <span className={"sx-dot" + (running ? " pulse" : "")} />
                  <span className="sx-host">{s.host}</span>
                  <span className="sx-status">{s.latest_run ? state : "no runs"}</span>
                </div>
                <div className="sx-metrics">
                  <div className="sx-metric">
                    <span className="sx-metric-n">{statText(s.files)}</span>
                    <span className="sx-metric-l">files</span>
                  </div>
                  <div className="sx-metric">
                    <span className="sx-metric-n ep">{statText(s.endpoints)}</span>
                    <span className="sx-metric-l">endpoints</span>
                  </div>
                  <div className="sx-metric">
                    <span className="sx-metric-n sec">{statText(s.secrets)}</span>
                    <span className="sx-metric-l">secrets</span>
                  </div>
                  <div className="sx-metric sx-metric-last">
                    <span className="sx-last">{relativeTime(lastRun)}</span>
                    <span className="sx-metric-l">last run</span>
                  </div>
                </div>
                <div className="sx-cov-bar">
                  <span style={{ width: `${s.coverage_pct ?? 0}%` }} />
                </div>
                <div className="sx-cov-l">
                  {s.coverage_pct == null ? "— attributed" : `${s.coverage_pct}% attributed`}
                </div>
              </div>

              <div className="sx-card-actions">
                <button type="button" className="sx-kebab" aria-label="Session actions"
                  aria-haspopup="menu" aria-expanded={menuFor === s.session_id}
                  onClick={() => setMenuFor(menuFor === s.session_id ? null : s.session_id)}>
                  <Icon name="more" size={15} />
                </button>
                {menuFor === s.session_id && (
                  <div className="sx-menu" role="menu">
                    <button type="button" role="menuitem" onClick={() => onRerun(s)}>
                      <Icon name="refresh" size={14} />Re-run
                    </button>
                    <button type="button" role="menuitem" onClick={() => onRename(s)}>
                      <Icon name="edit" size={14} />Rename
                    </button>
                    <button type="button" role="menuitem" onClick={() => onArchive(s)}>
                      <Icon name="archive" size={14} />{s.archived ? "Unarchive" : "Archive"}
                    </button>
                    <div className="sx-menu-sep" />
                    <button type="button" role="menuitem" className="danger" onClick={() => onDelete(s)}>
                      <Icon name="trash" size={14} />Delete
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
