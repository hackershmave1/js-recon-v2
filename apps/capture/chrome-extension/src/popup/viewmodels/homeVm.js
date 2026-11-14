// homeVm.js — builds the HomeView view-model from App state + actions.
// Pure function: no hooks, no side-effects. App calls this on every render.
import { scopeLabel, liveSubtitle, endedBanner, engagementName } from './captureVm.js';

export function buildHomeViewModel({
  // status (the worker's getStatus, incl. `capture` / `lastEnded`) + the live-ticking elapsed time
  status, elapsedMs, delivery, health, connectionLabel,
  // the capture flow (useCaptureFlow): phase, scope draft, Continue lookup, and its actions
  flow,
  // captures feed (analysisByUrl is pre-baked into captures by the caller)
  captures, mutedCount,
  // analysis
  analysis, canAnalyze,
  // D46: findings summary card — null until analysis is done and summary loaded
  findingsSummary,
  // engagement picker (a NEW session's engagement; locked once a capture runs)
  projects, projectId,
  // settings slices the vm needs
  settings,
  // actions
  reauth, openSettings, showAllCaptures, toggleSetting, exportNow, openWorkspace, analyzeNow,
  selectProject, createProject,
}) {
  const capture = status.capture || null;
  const files = status.fileCount || 0;
  return {
    flow,
    sessionExpired: status?.uploader?.authPaused === true,
    reauth,
    connectionLabel,
    deliveryHealth: health,
    delivery,
    // the capture in progress, in words (never a run ID)
    captureScope: capture ? scopeLabel(capture.scope) : '',
    captureSubtitle: capture ? liveSubtitle({ ...capture, elapsedMs }, files) : '',
    newSites: (capture && capture.newSites) || [],
    ended: endedBanner(status.lastEnded),
    everyTab: settings.captureEverything === true,
    projects,
    projectId,
    // The engagement the capture is bound to (the worker's binding), named even when the popup
    // can't resolve it — never shown as Solo when it is not.
    activeProjectName: engagementName(status.projectId, projects),
    selectProject,
    createProject,
    stats: { js: files, maps: status.mapsCount || 0, secrets: status.secretCount || 0 },
    captures,
    mutedCount,
    analysis,
    analyzeNow,
    canAnalyze,
    findingsSummary: findingsSummary || null,
    toggles: [
      { key: 'muteNoise', label: 'Mute plugins & trackers', on: settings.muteNoise === true },
      { key: 'captureAuthContext', label: 'Capture auth context', on: settings.captureAuthContext !== false },
      { key: 'exportIncludeContent', label: 'Include code in export', on: settings.exportIncludeContent === true },
    ],
    openSettings,
    showAllCaptures,
    toggleSetting,
    exportNow,
    openWorkspace,
  };
}
