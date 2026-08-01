import { useNavigate } from "react-router";
import { Icon } from "./icons";
import { EngagementSwitcher } from "../features/sessions/EngagementSwitcher";

// Left-nav sections. ANALYZE items view a run's data, so they navigate (scroll to the
// matching <section id> in app.tsx) only when a run is open; on the Sessions route they
// render inert. "Sessions" is a real cross-run route (its own GET /sessions page);
// "Threat Model" stays SOON (Slice 4) with no backend/page yet.
export type NavItem = { id: string; label: string; icon: string; soon?: boolean };
export const NAV_ITEMS: NavItem[] = [
  { id: "overview", label: "Overview", icon: "grid" },
  { id: "findings", label: "Findings", icon: "alert" },
  { id: "api-spec", label: "API Spec", icon: "code" },
  { id: "threat-model", label: "Threat Model", icon: "shield", soon: true },
  { id: "sources", label: "Sources", icon: "folder" },
];

export function Sidebar({ mode, runId, active, onNavigate }: {
  mode: "run" | "sessions";
  runId?: string;
  active: string;
  onNavigate: (id: string) => void;
}) {
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
          return (
            <button
              key={item.id}
              type="button"
              className={"shell-nav-item" + (active === item.id ? " is-active" : "")}
              aria-current={active === item.id ? "page" : undefined}
              onClick={() => onNavigate(item.id)}
            >
              <span className="shell-nav-ico"><Icon name={item.icon} /></span>
              <span className="shell-nav-txt">{item.label}</span>
            </button>
          );
        })}
        {/* Sessions is a real cross-run route, not a scroll target within a run. */}
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
