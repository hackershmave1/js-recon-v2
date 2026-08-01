import { useState, type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import "./shell.css";

// Frames the run workspace: fixed sidebar + top bar around one scrolling column of
// panels. Navigation is intra-page — clicking a nav item marks it active and scrolls
// the matching <section id> into view (no router, no route changes). scrollIntoView is
// only ever called from a click handler, never during render, so jsdom (which stubs it
// with a throwing no-op) is never exercised by the tests.
export function Shell({ runId, children }: { runId: string; children: ReactNode }) {
  const [active, setActive] = useState("overview");

  function navigate(sectionId: string) {
    setActive(sectionId);
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="shell">
      <Sidebar runId={runId} active={active} onNavigate={navigate} />
      <div className="shell-main">
        <TopBar onExport={() => navigate("api-spec")} />
        <div className="shell-view">
          <div className="shell-view-inner">{children}</div>
        </div>
      </div>
    </div>
  );
}
