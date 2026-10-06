// finding-labels.js — human labels for the platform's wire-level finding types, so the popup
// names a finding exactly as the web workspace does (QA: the findings card's "Top:" line showed
// the raw token `endpoint_unresolved` where the workspace says "suspected call"). Pure, so the
// popup imports it and a Node test pins it. KEEP IN SYNC with the workspace's single source of
// truth, apps/platform/web/src/api/findingLabels.ts (the extension is built separately and
// cannot import it).
export const TYPE_LABELS = {
  endpoint: 'API',
  endpoint_suspected: 'inferred API',
  endpoint_unresolved: 'suspected call',
  endpoint_generic: 'generic call',
  page_route: 'page route',
  secret_suspected: 'suspected secret',
  internal_ip: 'internal IP',
  graphql: 'GraphQL',
  postmessage_sink: 'postMessage',
  storage_sink: 'storage write'
};

// Unlabelled types (`secret`, `param`) read fine as-is, so they fall back to the wire token.
export function typeLabel(type) {
  return TYPE_LABELS[type] || String(type || '');
}
