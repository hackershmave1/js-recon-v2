import { useNavigate } from "react-router";
import { Icon } from "./icons";
import { PairDeviceButton } from "../features/capture/PairDeviceButton";

// The search pill is an inert placeholder (global search is a future slice) — a plain
// non-interactive div, so it can't trap focus or shadow the panels' own inputs. Export
// jumps to the run's API-Spec page and only shows in run mode (there is nothing to
// export without a run); New Recon is a real full-page link to "/" (the standalone New
// Run page).
export function TopBar({ mode = "run", runId }: {
  mode?: "run" | "sessions";
  runId?: string;
}) {
  const navigate = useNavigate();
  return (
    <header className="shell-top">
      <div className="shell-search" title="Search — coming soon">
        <Icon name="search" size={15} />
        <span className="shell-search-txt">Search findings, endpoints, files…</span>
        <kbd className="shell-kbd">⌘K</kbd>
      </div>
      <div className="shell-actions">
        <PairDeviceButton />
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
      </div>
    </header>
  );
}
