import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { pauseRun, cancelRun, resumeRun, ApiError } from "../../api/apiClient";
import { TERMINAL_STATES } from "../../api/types";
import type { RunControlResult } from "../../api/types";
import { ConfirmModal } from "../../shell/ConfirmModal";

export function RunControls(
  { runId, state, pauseRequested, cancelRequested, onControlResult }: {
    runId: string; state: string;
    pauseRequested: boolean; cancelRequested: boolean;
    onControlResult: (res: RunControlResult) => void;
  },
) {
  const { tenantId } = useTenant();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  if (TERMINAL_STATES.has(state)) return null;

  async function act(fn: (t: string, r: string) => Promise<RunControlResult>) {
    if (!tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      // Gating is driven by the POST's authoritative result (state + flags),
      // lifted to RunProgress — not by SSE, which never moves a non-terminal state.
      onControlResult(await fn(tenantId, runId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  // A cancel, once requested, is in flight until the run reaches a terminal state
  // (the worker cancels at its next checkpoint), so no other control is meaningful.
  if (cancelRequested) return <div className="muted">Cancelling…</div>;

  // A pause is cooperative: pause_requested can be set while the run stays active
  // until the worker checkpoints. Show it as pending (disabled) rather than a fresh
  // "Pause" that invites a redundant click — this is what survives a reload (A1).
  const paused = state === "paused";
  return (
    <div>
      {paused
        ? <button type="button" onClick={() => act(resumeRun)} disabled={busy}>Resume</button>
        : (
          <button type="button" onClick={() => act(pauseRun)} disabled={busy || pauseRequested}>
            {pauseRequested ? "Pausing…" : "Pause"}
          </button>
        )}
      <button type="button" onClick={() => setConfirmingCancel(true)} disabled={busy}>Cancel</button>
      {error && <span className="sev-high"> {error}</span>}
      {confirmingCancel && (
        <ConfirmModal
          title="Cancel this run?"
          message="This stops the run and can't be undone."
          confirmLabel="Cancel run"
          cancelLabel="Keep running"
          danger
          onConfirm={() => { setConfirmingCancel(false); void act(cancelRun); }}
          onCancel={() => setConfirmingCancel(false)}
        />
      )}
    </div>
  );
}
