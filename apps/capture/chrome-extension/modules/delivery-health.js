// delivery-health.js — derive operator-facing delivery health from the worker's getStatus
// payload. Pure (no chrome/fetch/DOM) so the popup imports it and a Node test can import it
// directly. This is the honesty half of D42: the worker already computes upload/skip/failure
// counts, but the popup never rendered them — so "captured nothing" and "never uploaded" looked
// identical to success.

// Per-file processing-failure codes recorded by background.recordProcessingFailure().
// Scope/denylist drops are NOT failures (they just `return`), so they never reach here.
export const SKIP_REASON_TEXT = {
  fetch_failed: 'fetch failed',
  decompress_failed: 'decompress failed',
  asset_too_large: 'file over size cap'
};

export function reasonText(reason) {
  if (!reason) return '';
  return SKIP_REASON_TEXT[reason] || String(reason);
}

// Collapse the worker status into the numbers + a single health verdict the popup renders.
//   uploaded  — files the backend acked (uploader.uploadedFiles)
//   pending   — outbox entries still waiting (uploader.pendingQueueLength)
//   dropped   — batches permanently dropped, e.g. a 4xx (uploader.droppedFiles)
//   skipped   — per-file processing failures: fetch/decompress/oversize (processingStats.failedFiles)
//   paired    — did the last save-files ack route to the operator tenant? null before first upload
// health precedence is fail > warn > ok: a hard delivery failure (upload error, a dropped batch,
// or an ack that landed in the shared tenant because the token is bad/expired) outranks a soft
// per-file skip, which outranks a clean state.
export function deriveDelivery(status) {
  const up = (status && status.uploader) || {};
  const ps = (status && status.processingStats) || {};
  const uploaded = up.uploadedFiles || 0;
  const pending = up.pendingQueueLength || 0;
  const dropped = up.droppedFiles || 0;
  const skipped = ps.failedFiles || 0;
  const failedTotal = dropped + skipped;
  const paired = typeof up.paired === 'boolean' ? up.paired : null;
  // Prefer the most actionable message: a live upload error, else the last processing reason.
  const lastReason = up.lastError || reasonText(ps.lastFailureReason) || '';
  const lastUrl = ps.lastFailureUrl || '';

  let health = 'ok';
  if (skipped > 0) health = 'warn';
  if (up.lastError || dropped > 0 || paired === false) health = 'fail';

  return { uploaded, pending, dropped, skipped, failedTotal, paired, lastReason, lastUrl, health };
}
