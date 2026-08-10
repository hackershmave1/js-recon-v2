import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import "./shell.css";

// Frames a view with a fixed sidebar + top bar around one scrolling column. Intra-run
// navigation is real routing now — each analysis view is its own route under
// /runs/:id, so an open Sources page no longer pushes Findings/API-Spec down a shared
// scroll. In "sessions" mode there is no active run: the analysis nav items are inert
// and the engagement switcher replaces the run card.
export function Shell({ runId, mode = "run", children }: {
  runId?: string;
  mode?: "run" | "sessions";
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <Sidebar mode={mode} runId={runId} />
      <div className="shell-main">
        <TopBar mode={mode} runId={runId} />
        <div className="shell-view">
          <div className="shell-view-inner">{children}</div>
        </div>
      </div>
    </div>
  );
}
