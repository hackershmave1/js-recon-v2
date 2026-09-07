// homeVm.js — builds the HomeView view-model from App state + actions.
// Pure function: no hooks, no side-effects. App calls this on every render.
export function buildHomeViewModel({
  // status
  status, activeHost, delivery, deliveryVm, outOfScopeHosts,
  scopeText, scopeMode, health, connectionLabel,
  // captures feed (analysisByUrl is pre-baked into captures by the caller)
  captures, mutedCount,
  // analysis
  analysis, canAnalyze,
  // D46: findings summary card — null until analysis is done and summary loaded
  findingsSummary,
  // engagement picker
  projects, projectId, activeProjectId, activeProjectName, overrides,
  // settings slices the vm needs
  settings,
  // actions
  reauth, armScope, addScopeHost, startNewSession,
  openSettings, toggleCapture, toggleSubdomains, showAllCaptures,
  toggleSetting, exportNow, openWorkspace, analyzeNow,
  selectProject, setOverride, clearOverride, createProject,
}) {
  return {
    capturing: status.isCapturing,
    sessionExpired: status?.uploader?.authPaused === true,
    reauth,
    connectionLabel,
    deliveryHealth: health,
    delivery: deliveryVm,
    host: status.host || activeHost || '—',
    session: (status.sessionId || '').slice(0, 8) || '—',
    scope: scopeText,
    scopeMode,
    activeHost,
    armScope,
    outOfScopeHosts,
    addScopeHost,
    includeSubdomains: settings.includeSubdomains !== false,
    startNewSession,
    startScopeDefault: (settings.domainScopes || []).join(', ') || activeHost || '',
    projects,
    projectId,
    activeProjectId,
    activeProjectName,
    selectProject,
    overrides,
    setOverride,
    clearOverride,
    createProject,
    stats: { js: status.fileCount || 0, maps: status.mapsCount || 0, secrets: status.secretCount || 0 },
    captures,
    mutedCount,
    analysis,
    analyzeNow,
    canAnalyze,
    findingsSummary: findingsSummary || null,
    toggles: [
      { key: 'captureEverything', label: 'Capture every tab (ignore scope)', on: settings.captureEverything === true },
      { key: 'performAnalysisOnUpload', label: 'Analyze on upload', on: settings.performAnalysisOnUpload === true },
      { key: 'muteNoise', label: 'Mute plugins & trackers', on: settings.muteNoise === true },
      { key: 'captureAuthContext', label: 'Capture auth context', on: settings.captureAuthContext !== false },
      { key: 'exportIncludeContent', label: 'Include code in export', on: settings.exportIncludeContent === true },
    ],
    openSettings,
    toggleCapture,
    toggleSubdomains,
    showAllCaptures,
    toggleSetting,
    exportNow,
    openWorkspace,
  };
}
