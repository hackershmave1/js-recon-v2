import { useState } from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { triageFinding, ApiError } from "../../api/apiClient";
import type { ReconstructedRequest } from "../../api/types";

// REQ-PB7: explicit ADR-0006 disclaimer, triage buttons, jump-to-source.
// Platform sends NO automated traffic. Operator fires from their own session.
export function ProbeHandoff({ req, runId, triageStatus, onTriaged }: {
  req: ReconstructedRequest;
  runId: string;
  triageStatus: string | null; // current triage status from optimistic overlay
  onTriaged: (hashes: string[], status: string) => void;
}) {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function triage(status: string) {
    if (!tenantId || req.endpoint_hashes.length === 0 || busy) return;
    setBusy(true); setError(null);
    try {
      await Promise.all(req.endpoint_hashes.map((h) => triageFinding(tenantId, runId, h, { status })));
      onTriaged(req.endpoint_hashes, status);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Triage failed");
    } finally {
      setBusy(false);
    }
  }

  function jumpToSource() {
    navigate(`/runs/${runId}/sources`);
  }

  return (
    <div className="pb-handoff">
      <p className="pb-handoff-notice">
        <strong>This platform sends no automated traffic.</strong> Copy an artifact above
        and fire the request from your own authenticated session.
      </p>
      {req.endpoint_hashes.length > 0 && (
        <div className="pb-handoff-actions">
          <span className="pb-handoff-label">Triage:</span>
          <button type="button" className="pb-triage-btn pb-triage-confirm"
            disabled={busy || triageStatus === "confirmed"}
            onClick={() => triage("confirmed")}>
            {triageStatus === "confirmed" ? "Confirmed ✓" : "Confirmed live"}
          </button>
          <button type="button" className="pb-triage-btn pb-triage-dismiss"
            disabled={busy || triageStatus === "dismissed"}
            onClick={() => triage("dismissed")}>
            {triageStatus === "dismissed" ? "Dismissed ✓" : "Dismiss"}
          </button>
          <button type="button" className="pb-jump" onClick={jumpToSource}>
            Jump to source
          </button>
          {error && <span className="pb-triage-error">{error}</span>}
        </div>
      )}
    </div>
  );
}
