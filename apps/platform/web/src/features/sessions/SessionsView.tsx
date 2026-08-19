import { Shell } from "../../shell/Shell";
import { useTenant } from "../../tenant/TenantContext";
import { SessionsPage } from "./SessionsPage";

// The /sessions route: the shell in "sessions" mode (no active run) wrapping the
// Sessions card grid. The engagement filter shared between the sidebar switcher and
// the grid is provided app-wide (EngagementFilterProvider, mounted in main.tsx) so the
// switcher is also live on the landing / New Run route, not just here.
export function SessionsView() {
  const { tenantId } = useTenant();
  return (
    <Shell mode="sessions">
      {tenantId && <SessionsPage tenantId={tenantId} />}
    </Shell>
  );
}
