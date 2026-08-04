import { useState } from "react";
import { createEngagement, ApiError } from "../../api/apiClient";
import type { Engagement } from "../../api/types";
import { Icon } from "../../shell/icons";

// New Engagement modal (design mockup lines 1197-1234). The mockup's "include
// subdomains" toggle is intentionally omitted: no backend honors it yet, so shipping it
// would be a control that lies about what it does (project rule §5). Scope here is
// organizational metadata — a run's enforced egress scope still comes from its session's
// scope host (REQ-P2).
export function NewEngagementModal({ tenantId, onClose, onCreated }: {
  tenantId: string;
  onClose: () => void;
  onCreated: (engagement: Engagement) => void;
}) {
  const [name, setName] = useState("");
  const [inScope, setInScope] = useState("");
  const [outScope, setOutScope] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

  async function submit() {
    if (!name.trim()) { setError("An engagement name is required"); return; }
    setBusy(true);
    setError(null);
    try {
      const engagement = await createEngagement(tenantId, {
        name: name.trim(),
        in_scope_domains: lines(inScope),
        out_of_scope_domains: lines(outScope),
      });
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
            Define the scope once. Every session and finding you collect lives under this umbrella.
          </div>
        </div>
        <div className="eng-modal-body">
          <label className="eng-modal-label" htmlFor="eng-name">ENGAGEMENT NAME</label>
          <input id="eng-name" className="eng-modal-input" value={name}
            onChange={(e) => setName(e.target.value)} placeholder="Acme Q3 Bug Bounty" />

          <label className="eng-modal-label" htmlFor="eng-in">
            IN-SCOPE DOMAINS <span className="eng-modal-hint">· one per line</span>
          </label>
          <textarea id="eng-in" className="eng-modal-area accent" value={inScope}
            onChange={(e) => setInScope(e.target.value)} placeholder={"api.acme.io\napp.acme.io"} />

          <label className="eng-modal-label" htmlFor="eng-out">
            OUT OF SCOPE <span className="eng-modal-hint">· optional</span>
          </label>
          <textarea id="eng-out" className="eng-modal-area warn" value={outScope}
            onChange={(e) => setOutScope(e.target.value)} placeholder={"blog.acme.io"} />

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
