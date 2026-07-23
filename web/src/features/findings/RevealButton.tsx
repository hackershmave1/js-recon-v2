import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { revealSecret, ApiError } from "../../api/apiClient";

const MESSAGES: Record<number, string> = {
  409: "The stored secret no longer matches (source changed).",
  410: "Evidence has been purged — cannot reveal.",
  422: "No stored location for this secret.",
  500: "Reveal failed — try again.",
};

export function RevealButton({ runId, hash }: { runId: string; hash: string }) {
  const { tenantId } = useTenant();
  const [value, setValue] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function reveal() {
    if (!tenantId) return;
    try {
      const res = await revealSecret(tenantId, runId, hash);
      setValue(res.value); setDone(true); setError(null);
    } catch (err) {
      if (err instanceof ApiError) setError(MESSAGES[err.status] ?? err.message);
      else setError("Reveal failed.");
    }
  }

  return (
    <div>
      <button type="button" onClick={reveal} disabled={done}>{done ? "Revealed" : "Reveal secret"}</button>
      {value && <code>{value}</code>}
      {error && <span className="sev-high"> {error}</span>}
    </div>
  );
}
