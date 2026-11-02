// D46(c) capture history rules: which findings summary a saved session row may carry, and which
// rows the popup may show. Pure (no chrome.*), so the rules are tested at runtime, not by regex.

export const CAPTURE_HISTORY_KEY = 'captureHistory';
export const HISTORY_MAX = 10;

// WorkspaceClient.getSessionFindingsSummary resolves { success, summary }. Cache only a finished
// summary, keyed by the session it describes, so it can never be attached to a later session.
export function summaryToCache(sessionId, result) {
  if (!sessionId || !result?.success || result.summary?.status !== 'complete') return null;
  return { sessionId, summary: result.summary };
}

export function summaryForSession(cached, sessionId) {
  return cached && cached.sessionId === sessionId ? cached.summary : null;
}

export function addToHistory(history, entry) {
  const next = [entry, ...(Array.isArray(history) ? history : [])];
  return next.slice(0, HISTORY_MAX);
}

// Rows are tagged with the tenant that captured them; another tenant's rows (and their [View]
// links, which that tenant can't open) stay hidden. Untagged rows predate tagging and their
// tenant is unknown, so they are hidden for everyone rather than risk a cross-tenant view.
export function historyForTenant(history, tenantId) {
  if (!Array.isArray(history)) return [];
  const current = tenantId || '';
  return history.filter((entry) => typeof entry?.tenantId === 'string' && entry.tenantId === current);
}
