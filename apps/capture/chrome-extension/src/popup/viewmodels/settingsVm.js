// settingsVm.js — builds the SettingsView view-model from App state + actions.
// Pure function: no hooks, no side-effects. App calls this on every render.
export function buildSettingsViewModel({
  closeSettings,
  connState, latency, testConnection,
  settings, status,
  signOut,
  patchSettings,
  newRule, setNewRule, addRule, removeRule,
  version,
}) {
  return {
    closeSettings,
    connState,
    latency,
    wsUrl: settings.workspaceUrl || '',
    setWsUrl: (v) => patchSettings({ workspaceUrl: v }),
    testConnection,
    authUser: settings.authUser || '',
    authTenantName: settings.authTenantName || '',
    signOut,
    paired: status?.uploader?.paired,
    // "Capture every tab" overrides the capture's scope while on (the scope still names the session).
    captureEverything: settings.captureEverything === true,
    setCaptureEverything: (on) => patchSettings({ captureEverything: on === true }),
    outOfScopeMode: settings.outOfScopeMode || 'tag',
    setOutOfScopeMode: (m) => patchSettings({ outOfScopeMode: m }),
    maxAssetMb: settings.maxAssetMb || 10,
    setMaxAssetMb: (n) => patchSettings({ maxAssetMb: n }),
    captureResponseBodies: settings.captureResponseBodies === true,
    toggleResponseBodies: () => patchSettings({ captureResponseBodies: !(settings.captureResponseBodies === true) }),
    denyDefaultProfile: settings.denyDefaultProfile !== false,
    toggleDefaultProfile: () => patchSettings({ denyDefaultProfile: !(settings.denyDefaultProfile !== false) }),
    denyRules: settings.denyRules || [],
    removeRule,
    newRule,
    setNewRule,
    addRule,
    // LLM provider config
    version,
  };
}
