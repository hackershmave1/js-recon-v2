import type { Engagement } from "../../api/types";
import { ConfirmModal } from "../../shell/ConfirmModal";
import { plural } from "./bulkDelete";

// What the confirm promises. Deleting an engagement DETACHES its sessions (#262): they move to
// "No engagement" and keep every run and finding. An older server sends no count, so the line
// then speaks of "its sessions" rather than inventing a number.
export function detachMessage(count: number | undefined): string {
  if (count === undefined) return "Its sessions will move to No engagement; nothing is deleted.";
  if (count === 0) return "No sessions are in it; nothing else is deleted.";
  return `${plural(count, "session")} will move to No engagement; nothing is deleted.`;
}

export function DeleteEngagementConfirm({ engagement, busy, error, onConfirm, onCancel }: {
  engagement: Engagement; busy: boolean; error: string | null;
  onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <ConfirmModal
      title={`Delete engagement "${engagement.name}"?`}
      message={detachMessage(engagement.session_count)}
      details={error ? <div className="eng-del-error" role="alert">{error}</div> : undefined}
      confirmLabel={busy ? "Deleting…" : "Delete engagement"}
      danger
      onConfirm={() => { if (!busy) onConfirm(); }}
      onCancel={onCancel}
    />
  );
}
