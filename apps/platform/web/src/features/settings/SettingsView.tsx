import { Shell } from "../../shell/Shell";
import { useTenant } from "../../tenant/TenantContext";
import { LocalTargetsCard } from "./LocalTargetsCard";
import { SettingsPage } from "./SettingsPage";
import { ThirdPartyRulesCard } from "./ThirdPartyRulesCard";

// The /settings route: the shell in "settings" mode (no active run), like /sessions.
export function SettingsView() {
  const { tenantId } = useTenant();
  return (
    <Shell mode="settings">
      {tenantId && <SettingsPage tenantId={tenantId} />}
      {tenantId && <ThirdPartyRulesCard tenantId={tenantId} />}
      <LocalTargetsCard />
    </Shell>
  );
}
