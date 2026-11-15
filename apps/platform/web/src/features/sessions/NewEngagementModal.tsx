import { useState } from "react";
import { createEngagement, ApiError } from "../../api/apiClient";
import type { Engagement } from "../../api/types";
import { Icon } from "../../shell/icons";

// New Engagement modal. An engagement is a name that groups sessions. It has no scope of its own:
// each session declares its scope when its capture starts (GLOSSARY "Session scope"), and a run's
// enforced egress scope comes from that session (REQ-P2). So there are no domain fields here.
export function NewEngagementModal({ tenantId, onClose, onCreated }: {
  tenantId: string;
  onClose: () => void;
  onCreated: (engagement: Engagement) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!name.trim()) { setError("An engagement name is required"); return; }
    setBusy(true);
    setError(null);
    try {
      const engagement = await createEngagement(tenantId, { name: name.trim() });
      onCreated(engagement);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create engagement");
      setBusy(false);
    }
  }

  return (
    <div className="eng-modal-scrim" onClick={onClose}>
      <div className="eng-modal" onClick={(e) => e.stopPropagation()}
        role="dialog" aria-modal="true" aria-label="New engagement">
        <div className="eng-modal-head">
          <h2>New engagement</h2>
          <div className="eng-modal-sub">
            A name that groups related sessions. Each session sets its own scope when its capture starts.
          </div>
        </div>
        <div className="eng-modal-body">
          <label className="eng-modal-label" htmlFor="eng-name">ENGAGEMENT NAME</label>
          <input id="eng-name" className="eng-modal-input" value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }} placeholder="Acme Q3 Bug Bounty" />
          {error && <div className="eng-modal-error">{error}</div>}
        </div>
        <div className="eng-modal-foot">
          <button type="button" className="eng-modal-cancel" onClick={onClose}>Cancel</button>
          <button type="button" className="eng-modal-create btn-primary"
            onClick={submit} disabled={busy || !name.trim()}>
            <Icon name="arrow-right" size={15} />
            {busy ? "Creating…" : "Create engagement"}
          </button>
        </div>
      </div>
    </div>
  );
}
