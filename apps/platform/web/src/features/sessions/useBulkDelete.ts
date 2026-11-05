import { useState } from "react";
import { bulkDeleteSessions } from "../../api/apiClient";
import type { SessionSummary } from "../../api/types";
import { deleteInBatches, outcomeText, selectedVisibleIds } from "./bulkDelete";

// The Sessions page's multi-select + bulk delete state. Only selected sessions the current view
// SHOWS are ever acted on (selectedVisibleIds), so switching engagement can't delete hidden ones.
export function useBulkDelete(tenantId: string, visible: SessionSummary[], reload: () => void) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string | null>(null);

  const ids = selectedVisibleIds(selected, visible);
  const allSelected = visible.length > 0 && ids.length === visible.length;
  const busy = deleting.size > 0;

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(visible.map((s) => s.session_id)));
  }
  // A new view (archived toggle) starts clean: no carried selection, no stale outcome line.
  function resetView() {
    setSelected(new Set());
    setStatus(null);
  }
  // After a reload: a selection never outlives its session.
  function prune(present: ReadonlySet<string>) {
    setSelected((prev) => new Set([...prev].filter((id) => present.has(id))));
  }

  async function run() {
    const batchIds = ids;
    setConfirmOpen(false);
    setDeleting(new Set(batchIds));
    setStatus(`Deleting 0/${batchIds.length}…`);
    const result = await deleteInBatches(
      batchIds,
      (batch) => bulkDeleteSessions(tenantId, batch),
      (done) => setStatus(`Deleting ${done}/${batchIds.length}…`),
    );
    // Failed ones stay selected so a retry is one click; the rest leave the selection.
    const failed = new Set(result.failed.map((f) => f.id));
    setSelected((prev) => new Set([...prev].filter((id) => failed.has(id))));
    setDeleting(new Set());
    setStatus(outcomeText(result));
    reload(); // whatever the outcome: the list shows what actually survived
  }

  return {
    selected, ids, allSelected, busy, deleting, status, confirmOpen,
    toggle, toggleAll, resetView, prune, run,
    openConfirm: () => setConfirmOpen(true),
    closeConfirm: () => setConfirmOpen(false),
  };
}
