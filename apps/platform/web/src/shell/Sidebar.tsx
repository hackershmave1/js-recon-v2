import { NavLink, useNavigate } from "react-router";
import { Icon } from "./icons";
import { EngagementSwitcher } from "../features/sessions/EngagementSwitcher";

// Left-nav sections. ANALYZE items view a run's data, so each is a real route under
// /runs/:id (Overview is the index route, the rest are child segments); on the
// Sessions route they render inert. "Sessions" is a real cross-run route (its own
// GET /sessions page); "Threat Model" stays SOON (Slice 4) with no backend/page yet.
export type NavItem = { id: string; label: string; icon: string; soon?: boolean };
export const NAV_ITEMS: NavItem[] = [
  { id: "overview", label: "Overview", icon: "grid" },
  { id: "findings", label: "Findings", icon: "alert" },
  { id: "api-spec", label: "API Spec", icon: "code" },
  { id: "threat-model", label: "Threat Model", icon: "shield", soon: true },
  { id: "sources", label: "Sources", icon: "folder" },
];

export function Sidebar({ mode, runId }: { mode: "run" | "sessions"; runId?: string }) {
  const navigate = useNavigate();
  const inRun = mode === "run";
  return (
    <aside className="shell-side">
      <div className="shell-brand">
        <span className="shell-brand-mark"><Icon name="search" size={17} /></span>
        <div>
          <div className="shell-brand-name">RECON</div>
          <div className="shell-brand-sub">WORKSPACE</div>
        </div>
      </div>

      <nav className="shell-nav" aria-label="Analyze">
        <div className="shell-nav-label">ANALYZE</div>
        {NAV_ITEMS.map((item) => {
          if (item.soon) {
            return (
              <div key={item.id} className="shell-nav-item is-soon" aria-disabled="true">
                <span className="shell-nav-ico"><Icon name={item.icon} /></span>
                <span className="shell-nav-txt">{item.label}</span>
                <span className="shell-soon">SOON</span>
              </div>
            );
          }
          if (!inRun) {
            // The analysis views need a selected run; on /sessions they are inert.
            return (
              <div key={item.id} className="shell-nav-item is-inert" aria-disabled="true"
                title="Open a session to view its analysis">
                <span className="shell-nav-ico"><Icon name={item.icon} /></span>
                <span className="shell-nav-txt">{item.label}</span>
              </div>
            );
          }
          // Overview is the index route (/runs/:id); the rest are child segments.
          const seg = item.id === "overview" ? "" : item.id;
          return (
            <NavLink
              key={item.id}
              to={`/runs/${runId}${seg ? `/${seg}` : ""}`}
              end={item.id === "overview"}
              className={({ isActive }) => "shell-nav-item" + (isActive ? " is-active" : "")}
            >
              <span className="shell-nav-ico"><Icon name={item.icon} /></span>
              <span className="shell-nav-txt">{item.label}</span>
            </NavLink>
          );
        })}
        {/* Sessions is a real cross-run route, not a view within a run. */}
        <button
          type="button"
          className={"shell-nav-item" + (mode === "sessions" ? " is-active" : "")}
          aria-current={mode === "sessions" ? "page" : undefined}
          onClick={() => navigate("/sessions")}
        >
          <span className="shell-nav-ico"><Icon name="layers" /></span>
          <span className="shell-nav-txt">Sessions</span>
        </button>
      </nav>

      <div className="shell-eng">
        <div className="shell-nav-label">ENGAGEMENT</div>
        {inRun ? (
          <div className="shell-eng-card">
            <span className="shell-eng-mark"><Icon name="folder" size={15} /></span>
            <span className="shell-eng-body">
              <span className="shell-eng-name">Current run</span>
              <span className="shell-eng-id" title={runId}>{runId ? runId.slice(0, 8) : "—"}</span>
            </span>
          </div>
        ) : (
          <EngagementSwitcher />
        )}
      </div>
    </aside>
  );
}
