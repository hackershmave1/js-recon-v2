import { useState } from "react";
import { Shell } from "../../shell/Shell";
import { useTenant } from "../../tenant/TenantContext";
import { EngagementFilterContext, ENGAGEMENT_STORAGE_KEY } from "./engagementFilter";
import { SessionsPage } from "./SessionsPage";

// The /sessions route: the shell in "sessions" mode (no active run) wrapping the
// Sessions card grid, with the engagement filter shared between the sidebar switcher
// and the grid. The selection persists to localStorage (see engagementFilter).
export function SessionsView() {
  const { tenantId } = useTenant();
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
      <Shell mode="sessions">
        {tenantId && <SessionsPage tenantId={tenantId} />}
      </Shell>
    </EngagementFilterContext.Provider>
  );
}
