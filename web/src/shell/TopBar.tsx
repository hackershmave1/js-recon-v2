import { Icon } from "./icons";

// The search pill is an inert placeholder (global search is a future slice) — a plain
// non-interactive div, so it can't trap focus or shadow the panels' own inputs. Export
// scrolls to the API-Spec/export section and only shows in run mode (there is nothing to
// export without a run); New Recon is a real full-page link to "/" (the standalone New
// Run page) so the shell needs no router dependency.
export function TopBar({ mode = "run", onExport }: {
  mode?: "run" | "sessions";
  onExport: () => void;
}) {
  return (
    <header className="shell-top">
      <div className="shell-search" title="Search — coming soon">
        <Icon name="search" size={15} />
        <span className="shell-search-txt">Search findings, endpoints, files…</span>
        <kbd className="shell-kbd">⌘K</kbd>
      </div>
      <div className="shell-actions">
        {mode === "run" && (
          <button type="button" className="shell-btn" onClick={onExport}>
            <Icon name="download" size={15} />
            Export
          </button>
        )}
        <a className="shell-btn shell-btn-primary" href="/">
          <Icon name="plus" size={15} />
          New Recon
        </a>
      </div>
    </header>
  );
}
