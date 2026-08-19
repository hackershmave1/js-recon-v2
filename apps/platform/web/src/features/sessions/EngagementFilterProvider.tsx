import { useState, type ReactNode } from "react";
import { EngagementFilterContext, ENGAGEMENT_STORAGE_KEY } from "./engagementFilter";

// Holds the engagement-filter selection for the whole app so the sidebar switcher is
// live on every route that renders it — the landing / New Run route (`/`) and
// `/sessions` — not only the page that happened to own the state. Mounted once at the
// app root (main.tsx); the selection is mirrored to localStorage so NewRunPanel's
// submit (a plain read of ENGAGEMENT_STORAGE_KEY) attaches new runs to the active
// engagement regardless of which route it was picked on.
export function EngagementFilterProvider({ children }: { children: ReactNode }) {
  const [engagementId, setId] = useState<string | null>(
    () => localStorage.getItem(ENGAGEMENT_STORAGE_KEY),
  );
  function setEngagementId(id: string | null) {
    if (id) localStorage.setItem(ENGAGEMENT_STORAGE_KEY, id);
    else localStorage.removeItem(ENGAGEMENT_STORAGE_KEY);
    setId(id);
  }
  return (
    <EngagementFilterContext.Provider value={{ engagementId, setEngagementId }}>
      {children}
    </EngagementFilterContext.Provider>
  );
}
