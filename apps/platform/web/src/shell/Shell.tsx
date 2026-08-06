import { createContext, useState, type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import "./shell.css";

// Lets a deep child trigger the shell's section scroll. The jump-to-source flow
// (a finding occurrence -> Sources) calls this from an effect to reveal the
// Sources section; default is a no-op so a child outside a Shell is inert.
export const ShellNavContext = createContext<(sectionId: string) => void>(() => {});

// Frames a view with a fixed sidebar + top bar around one scrolling column. In "run"
// mode (a run workspace) intra-page nav marks a nav item active and scrolls the matching
// <section id> into view (no router, no route changes); scrollIntoView is only ever
// called from a click handler, never during render, so jsdom (which stubs it with a
// throwing no-op) is never exercised. In "sessions" mode there is no active run: the
// analysis nav items are inert and the engagement switcher replaces the run card.
export function Shell({ runId, mode = "run", children }: {
  runId?: string;
  mode?: "run" | "sessions";
  children: ReactNode;
}) {
  const [active, setActive] = useState("overview");

  function navigate(sectionId: string) {
    setActive(sectionId);
    // Now also called from an effect (jump-to-source), not only a click, so guard
    // it: jsdom stubs scrollIntoView as a throwing no-op.
    try {
      document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch { /* jsdom stubs scrollIntoView as a throwing no-op */ }
  }

  return (
    <div className="shell">
      <Sidebar mode={mode} runId={runId} active={active} onNavigate={navigate} />
      <div className="shell-main">
        <TopBar mode={mode} onExport={() => navigate("api-spec")} />
        <div className="shell-view">
          <div className="shell-view-inner">
            <ShellNavContext.Provider value={navigate}>{children}</ShellNavContext.Provider>
          </div>
        </div>
      </div>
    </div>
  );
}
