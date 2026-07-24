import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { triageFinding, ApiError } from "../../api/apiClient";
import { TRIAGE_STATUSES } from "../../api/types";

export function TriageControls({ runId, hash, current }: { runId: string; hash: string; current: string }) {
  const { tenantId } = useTenant();
  const [status, setStatus] = useState(current);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!tenantId) return;
    try {
      const res = await triageFinding(tenantId, runId, hash, { status, note: note || undefined });
      setSaved(res.status); setError(null);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Triage failed"); }
  }

  return (
    <div>
      <label htmlFor={`tr-${hash}`}>Triage</label>
      <select id={`tr-${hash}`} value={status} onChange={(e) => setStatus(e.target.value)}>
        {TRIAGE_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <input aria-label="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (optional)" />
      <button type="button" onClick={save}>Save</button>
      {saved && <span className="muted"> saved: {saved}</span>}
      {error && <span className="sev-high"> {error}</span>}
    </div>
  );
}
