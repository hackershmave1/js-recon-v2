import { Shell } from "../../shell/Shell";
import { useTenant } from "../../tenant/TenantContext";
import { SettingsPage } from "./SettingsPage";

// The /settings route: the shell in "settings" mode (no active run), like /sessions.
export function SettingsView() {
  const { tenantId } = useTenant();
  return (
    <Shell mode="settings">
      {tenantId && <SettingsPage tenantId={tenantId} />}
    </Shell>
  );
}
