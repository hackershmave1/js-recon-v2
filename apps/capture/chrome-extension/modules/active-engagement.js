// Pure helpers for the popup's "active engagement" display. No chrome/fetch/DOM, so they are
// unit-tested directly (node tests/test_active_engagement_reconcile.mjs) and imported by the
// Preact popup. The active engagement's identity is the projectId the worker already holds
// (getStatus); these helpers derive the DISPLAY from the current engagements ("projects") list,
// so nothing is persisted twice (a persisted name would go stale on a workspace rename).

// Reconcile the active projectId against the current engagements list. Returns the id if it is
// still a real engagement, else null (Standalone). This drops a dangling id after the engagement
// is deleted or after re-login as a different tenant, so the UI never shows a phantom engagement.
export function reconcileActiveProject(projectId, projects) {
  if (!projectId) return null;
  const list = Array.isArray(projects) ? projects : [];
  return list.some((p) => p && p.id === projectId) ? projectId : null;
}

// Display name for the active engagement, taken live from the engagements list. '' for Standalone
// or an unknown id (callers render "Solo · standalone" for the empty case).
export function activeProjectName(projectId, projects) {
  if (!projectId) return '';
  const list = Array.isArray(projects) ? projects : [];
  const match = list.find((p) => p && p.id === projectId);
  return (match && match.name) || '';
}
