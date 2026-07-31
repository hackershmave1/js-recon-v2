import { useEffect, useState } from "react";
import type React from "react";
import { useTenant } from "../../tenant/TenantContext";
import { addWrapperRule, deleteWrapperRule, listWrapperRules, ApiError } from "../../api/apiClient";
import type { WrapperRule } from "../../api/types";

export function WrapperPanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [rules, setRules] = useState<WrapperRule[]>([]);
  const [callee, setCallee] = useState("");
  const [recovered, setRecovered] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    listWrapperRules(tenantId, runId).then(setRules).catch(() => { /* first load best-effort */ });
  }, [tenantId, runId]);

  const ready = callee.trim() !== "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      const res = await addWrapperRule(tenantId, runId, { callee: callee.trim() });
      setRules((prev) => [...prev.filter((r) => r.callee !== res.rule.callee), res.rule]);
      setRecovered(res.recovered);
      setCallee("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add wrapper");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      await deleteWrapperRule(tenantId, runId, id);
      setRules((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete wrapper");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>HTTP-client wrapper</h3>
      <p className="muted">Teach the extractor a custom client so <code>api.get('/x')</code>-style calls become endpoints.</p>
      <div>
        <label htmlFor="wrapper-callee">Wrapper callee</label>
        <input id="wrapper-callee" value={callee} onChange={(e) => setCallee(e.target.value)}
          placeholder="api" />
      </div>
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>{busy ? "Teaching…" : "Teach wrapper"}</button>
      {recovered !== null && <p className="muted">recovered {recovered} rows</p>}
      <ul>
        {rules.map((r) => (
          <li key={r.id}>
            <code>{r.callee}</code>
            <button type="button" onClick={() => remove(r.id)} aria-label={`Delete wrapper ${r.callee}`}>Delete</button>
          </li>
        ))}
      </ul>
    </form>
  );
}
