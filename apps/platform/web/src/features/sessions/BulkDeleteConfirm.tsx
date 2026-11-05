import type { SessionSummary } from "../../api/types";
import { ConfirmModal } from "../../shell/ConfirmModal";
import { plural } from "./bulkDelete";

const SHOWN = 10;

// One confirm for a multi-select delete. Hosts repeat across sessions, so each line carries the
// session name (when set) and its created date. Extension captures are called out: a capture still
// uploading recreates its session on the next batch, so it may reappear after the delete.
export function BulkDeleteConfirm({ sessions, onConfirm, onCancel }: {
  sessions: SessionSummary[]; onConfirm: () => void; onCancel: () => void;
}) {
  const captures = sessions.filter((s) => s.external_id).length;
  return (
    <ConfirmModal
      title={`Delete ${plural(sessions.length, "session")}?`}
      message={`This permanently deletes ${plural(sessions.length, "session")} and all their runs. This can't be undone.`}
      details={
        <div className="sx-bulk-details">
          <ul className="sx-bulk-list">
            {sessions.slice(0, SHOWN).map((s) => (
              <li key={s.session_id}>
                {s.name && s.name !== s.host ? `${s.name} · ` : ""}{s.host}
                {s.created_at && <span className="sx-bulk-date"> · {s.created_at.slice(0, 10)}</span>}
              </li>
            ))}
          </ul>
          {sessions.length > SHOWN && <div className="sx-bulk-more">and {sessions.length - SHOWN} more</div>}
          {captures > 0 && (
            <div className="sx-bulk-warn">
              {captures} {captures === 1 ? "is an extension capture" : "are extension captures"}: if
              the extension is still capturing, {captures === 1 ? "it" : "they"} may reappear.
            </div>
          )}
        </div>
      }
      confirmLabel="Delete"
      danger
      onConfirm={() => onConfirm()}
      onCancel={onCancel}
    />
  );
}
