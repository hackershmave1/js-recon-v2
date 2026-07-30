import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { pauseRun, cancelRun, resumeRun, ApiError } from "../../api/apiClient";
import { TERMINAL_STATES } from "../../api/types";
import type { RunControlResult } from "../../api/types";

export function RunControls(
  { runId, state, onStateChange }: { runId: string; state: string; onStateChange: (s: string) => void },
) {
  const { tenantId } = useTenant();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (TERMINAL_STATES.has(state)) return null;

  async function act(fn: (t: string, r: string) => Promise<RunControlResult>, confirmMsg?: string) {
    if (!tenantId || busy) return;
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true); setError(null);
    try {
      const res = await fn(tenantId, runId);
      onStateChange(res.state);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {state === "paused"
        ? <button type="button" onClick={() => act(resumeRun)} disabled={busy}>Resume</button>
        : <button type="button" onClick={() => act(pauseRun)} disabled={busy}>Pause</button>}
      <button type="button" onClick={() => act(cancelRun, "Cancel this run? This cannot be undone.")} disabled={busy}>Cancel</button>
      {error && <span className="sev-high"> {error}</span>}
    </div>
  );
}
