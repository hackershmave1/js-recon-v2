import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { Icon } from "./icons";
import { useAuth } from "../auth/AuthProvider";
import { useTenant } from "../tenant/TenantContext";
import { listSessions } from "../api/apiClient";
import type { SessionSummary } from "../api/types";
import { matchSessions } from "./searchSessions";

// D54: a real client-side session search (was an inert "coming soon" div). Sessions load
// lazily on first focus; matches on name / host / scope / external id; picking one jumps to
// its latest run (or the Sessions list if it has no run yet). Client-side "to start" — a
// server-side findings/endpoints/files index is the follow-up.
function GlobalSearch() {
  const navigate = useNavigate();
  const { tenantId } = useTenant();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loaded, setLoaded] = useState(false);

  function ensureLoaded() {
    if (loaded || !tenantId) return;
    setLoaded(true);
    listSessions(tenantId).then((r) => setSessions(r.sessions)).catch(() => {});
  }

  const matches = useMemo(() => matchSessions(sessions, q), [q, sessions]);

  function go(s: SessionSummary) {
    setOpen(false);
    setQ("");
    navigate(s.latest_run ? `/runs/${s.latest_run.run_id}` : "/sessions");
  }

  return (
    <div
      className="shell-search-wrap"
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false); }}
    >
      <div className="shell-search">
        <Icon name="search" size={15} />
        <input
          className="shell-search-input"
          value={q}
          placeholder="Search sessions by name, host…"
          aria-label="Search sessions"
          onFocus={() => { ensureLoaded(); setOpen(true); }}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        />
      </div>
      {open && q.trim() !== "" && (
        <ul className="shell-search-results">
          {matches.length === 0 ? (
            <li className="shell-search-empty">No sessions match.</li>
          ) : (
            matches.map((s) => (
              <li key={s.session_id}>
                {/* preventDefault on mousedown so the click lands before the wrap's onBlur closes us */}
                <button
                  type="button"
                  className="shell-search-item"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => go(s)}
                >
                  <span className="shell-search-name">{s.name || s.host || s.session_id.slice(0, 8)}</span>
                  {s.host && <span className="shell-search-host">{s.host}</span>}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

// Export jumps to the run's API-Spec page and only shows in run mode (there is nothing to
// export without a run); New Recon is a real full-page link to "/" (the standalone New Run page).
export function TopBar({ mode = "run", runId }: {
  mode?: "run" | "sessions";
  runId?: string;
}) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  return (
    <header className="shell-top">
      <GlobalSearch />
      <div className="shell-actions">
        {mode === "run" && runId && (
          <button type="button" className="shell-btn" onClick={() => navigate(`/runs/${runId}/api-spec`)}>
            <Icon name="download" size={15} />
            Export
          </button>
        )}
        <a className="shell-btn shell-btn-primary" href="/">
          <Icon name="plus" size={15} />
          New Recon
        </a>
        {user && (
          <span
            className="shell-user"
            title={`Signed in as ${user.username || "user"} (${user.role})`}
            style={{ alignSelf: "center", opacity: 0.75, fontSize: 13, whiteSpace: "nowrap" }}
          >
            {user.username || "user"}
            {user.tenantName ? ` · ${user.tenantName}` : ""}
          </span>
        )}
        {user && (
          <button type="button" className="shell-btn" onClick={logout} title="Log out">
            Log out
          </button>
        )}
      </div>
    </header>
  );
}
