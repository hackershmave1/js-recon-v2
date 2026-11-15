import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router";
import { Icon } from "./icons";
import { EngagementSwitcher } from "../features/sessions/EngagementSwitcher";
import { useRunDataOptional } from "../features/progress/runData";
import { getSessionRuns } from "../api/apiClient";
import type { SessionEngagementRef } from "../api/types";
import { useTenant } from "../tenant/TenantContext";
import { techStackHidden } from "./techVisibility";

// The crawl target as a bare host for display ("http://recon-range.test/" ->
// "recon-range.test"); a bare host or unparseable value is shown as-is, null stays null.
function hostOf(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try { return new URL(raw).host || raw; } catch { return raw; }
}

// What the card under ENGAGEMENT says on a run page. The heading promises an engagement, so the
// first line IS the run's engagement: its name, "No engagement" for a standalone session, and
// nothing claimed while that is still unknown (not loaded, or a server that doesn't say). The
// second line is the "what am I looking at": the run's target and its short id.
export function runCardLines({ engagement, domain, runId }: {
  engagement: SessionEngagementRef | null | undefined; domain: string | null; runId?: string;
}): { name: string; detail: string } {
  const name = engagement ? engagement.name : engagement === null ? "No engagement" : "—";
  const detail = [domain, runId ? runId.slice(0, 8) : null].filter(Boolean).join(" · ") || "Current run";
  return { name, detail };
}

// The persistent card on a run page: the engagement the run's session belongs to, above the
// run's crawl target (from the assets manifest the run stream already holds) and run id. Only
// mounted in run mode, so the optional hook is non-null here.
function CurrentRunCard({ runId }: { runId?: string }) {
  const { tenantId } = useTenant();
  const run = useRunDataOptional();
  const sessionId = run?.sessionId ?? null;
  const [engagement, setEngagement] = useState<SessionEngagementRef | null | undefined>(undefined);
  useEffect(() => {
    setEngagement(undefined);
    if (!tenantId || !sessionId) return;
    let live = true;
    getSessionRuns(tenantId, sessionId)
      .then((r) => { if (live) setEngagement(r.engagement); })
      .catch(() => { /* unknown stays unknown: never shown as "No engagement" */ });
    return () => { live = false; };
  }, [tenantId, sessionId]);
  const { name, detail } = runCardLines({ engagement, domain: hostOf(run?.assets?.domain), runId });
  return (
    <div className="shell-eng-card">
      <span className="shell-eng-mark"><Icon name="folder" size={15} /></span>
      <span className="shell-eng-body">
        <span className="shell-eng-name" title={name}>{name}</span>
        <span className="shell-eng-id" title={runId}>{detail}</span>
      </span>
    </div>
  );
}

// Left-nav sections. ANALYZE items view a run's data, so each is a real route under
// /runs/:id (Overview is the index route, the rest are child segments); on the
// Sessions route they render inert. "Sessions" is a real cross-run route (its own
// GET /sessions page).
export type NavItem = { id: string; label: string; icon: string; soon?: boolean };
export const NAV_ITEMS: NavItem[] = [
  { id: "overview", label: "Overview", icon: "grid" },
  { id: "findings", label: "Findings", icon: "alert" },
  { id: "api-spec", label: "API Spec", icon: "code" },
  { id: "graphql", label: "GraphQL", icon: "link" },
  { id: "probe", label: "Probe", icon: "target" },
  { id: "tech", label: "Tech stack", icon: "layers" },
  { id: "hosts", label: "Hosts", icon: "globe" },
  { id: "threat-model", label: "Threat Model", icon: "shield" },
  { id: "sources", label: "Sources", icon: "folder" },
];

export function Sidebar({ mode, runId }: { mode: "run" | "sessions" | "settings"; runId?: string }) {
  const navigate = useNavigate();
  const inRun = mode === "run";
  const run = useRunDataOptional();
  // A run that finished without technology detection (extension captures) has nothing to show there.
  const navItems = NAV_ITEMS.filter((item) => !(item.id === "tech" && techStackHidden(run?.technologies, run?.state)));
  return (
    <aside className="shell-side">
      <div className="shell-brand">
        <span className="shell-brand-mark">
          <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M2 12C5 6 19 6 22 12C19 18 5 18 2 12Z"
                  stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" fill="currentColor" fillOpacity="0.12" />
            <circle cx="12" cy="12" r="3.6" stroke="currentColor" strokeWidth="1.8" />
            <circle cx="12" cy="12" r="1.5" fill="currentColor" />
          </svg>
        </span>
        <div>
          <div className="shell-brand-name">RECON</div>
          <div className="shell-brand-sub">WORKSPACE</div>
        </div>
      </div>

      <nav className="shell-nav" aria-label="Analyze">
        <div className="shell-nav-label">ANALYZE</div>
        {navItems.map((item) => {
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
        <button
          type="button"
          className={"shell-nav-item" + (mode === "settings" ? " is-active" : "")}
          aria-current={mode === "settings" ? "page" : undefined}
          onClick={() => navigate("/settings")}
        >
          <span className="shell-nav-ico"><Icon name="gear" /></span>
          <span className="shell-nav-txt">Settings</span>
        </button>
      </nav>

      <div className="shell-eng">
        <div className="shell-nav-label">ENGAGEMENT</div>
        {inRun ? (
          <CurrentRunCard runId={runId} />
        ) : (
          <EngagementSwitcher />
        )}
      </div>
    </aside>
  );
}
