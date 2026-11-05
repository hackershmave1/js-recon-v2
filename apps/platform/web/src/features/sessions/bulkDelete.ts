// Bulk session delete: the Sessions page's multi-select sends its selection to
// POST /sessions/bulk-delete in bounded batches. Every server-side delete purges its runs' stored
// files synchronously, so one huge request could outlive a proxy timeout and lose the report of
// what was deleted; batches keep each request short and give the page real progress to show.
import type { BulkDeleteResponse } from "../../api/types";

export const BATCH_SIZE = 25; // well under the server's 50-per-request cap

export async function deleteInBatches(
  ids: string[],
  deleteBatch: (batch: string[]) => Promise<BulkDeleteResponse>,
  onProgress: (done: number) => void,
): Promise<BulkDeleteResponse> {
  const merged: BulkDeleteResponse = { deleted: [], not_found: [], failed: [] };
  for (let start = 0; start < ids.length; start += BATCH_SIZE) {
    const batch = ids.slice(start, start + BATCH_SIZE);
    try {
      const r = await deleteBatch(batch);
      merged.deleted.push(...r.deleted);
      merged.not_found.push(...r.not_found);
      merged.failed.push(...r.failed);
    } catch {
      // The request itself failed (network, timeout): its outcome is unknown, so report the batch
      // as failed — the list reload afterwards shows what actually survived.
      merged.failed.push(...batch.map((id) => ({ id, error: "request_failed" })));
    }
    onProgress(Math.min(start + BATCH_SIZE, ids.length));
  }
  return merged;
}

// Only what the operator can SEE: a selection made before switching engagement (or toggling
// archived) must never delete sessions the current view hides.
export function selectedVisibleIds(
  selected: ReadonlySet<string>,
  visible: readonly { session_id: string }[],
): string[] {
  return visible.filter((s) => selected.has(s.session_id)).map((s) => s.session_id);
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function outcomeText(r: BulkDeleteResponse): string {
  const parts = [`Deleted ${plural(r.deleted.length, "session")}`];
  if (r.failed.length) parts.push(`${r.failed.length} failed`);
  if (r.not_found.length) parts.push(`${r.not_found.length} already gone`);
  return parts.join(" · ");
}
