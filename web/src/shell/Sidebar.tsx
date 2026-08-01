import { Icon } from "./icons";

// Left-nav sections. `soon` items have no backend/page yet (Threat Model = Slice 4;
// Sessions = its own GET /sessions slice) — they render inert with a SOON tag rather
// than linking to a fake page. The non-soon ids match the <section id> wrappers in
// app.tsx so a click can scroll the matching panel into view.
export type NavItem = { id: string; label: string; icon: string; soon?: boolean };
export const NAV_ITEMS: NavItem[] = [
  { id: "overview", label: "Overview", icon: "grid" },
  { id: "findings", label: "Findings", icon: "alert" },
  { id: "api-spec", label: "API Spec", icon: "code" },
  { id: "threat-model", label: "Threat Model", icon: "shield", soon: true },
  { id: "sources", label: "Sources", icon: "folder" },
  { id: "sessions", label: "Sessions", icon: "layers", soon: true },
];

export function Sidebar({ runId, active, onNavigate }: {
  runId: string;
  active: string;
  onNavigate: (id: string) => void;
}) {
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
        {NAV_ITEMS.map((item) =>
          item.soon ? (
            <div key={item.id} className="shell-nav-item is-soon" aria-disabled="true">
              <span className="shell-nav-ico"><Icon name={item.icon} /></span>
              <span className="shell-nav-txt">{item.label}</span>
              <span className="shell-soon">SOON</span>
            </div>
          ) : (
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
          ),
        )}
      </nav>

      <div className="shell-eng">
        <div className="shell-nav-label">ENGAGEMENT</div>
        <div className="shell-eng-card">
          <span className="shell-eng-mark"><Icon name="folder" size={15} /></span>
          <span className="shell-eng-body">
            <span className="shell-eng-name">Current run</span>
            <span className="shell-eng-id" title={runId}>{runId.slice(0, 8)}</span>
          </span>
        </div>
      </div>
    </aside>
  );
}
