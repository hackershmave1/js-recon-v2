import { useEffect, useState } from "react";
import type React from "react";
import { useTenant } from "../../tenant/TenantContext";
import { addBaseUrlRule, deleteBaseUrlRule, listBaseUrlRules, ApiError } from "../../api/apiClient";
import type { BaseUrlRule, SpecSummary } from "../../api/types";

export function BaseUrlPanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [rules, setRules] = useState<BaseUrlRule[]>([]);
  const [prefix, setPrefix] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [summary, setSummary] = useState<SpecSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    listBaseUrlRules(tenantId, runId).then(setRules).catch(() => { /* first load best-effort */ });
  }, [tenantId, runId]);

  const ready = prefix.trim() !== "" && baseUrl.trim() !== "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      const res = await addBaseUrlRule(tenantId, runId, {
        kind: "prefix", path_prefix: prefix, base_url: baseUrl,
      });
      setRules((prev) => [...prev.filter((r) => r.path_prefix !== res.rule.path_prefix), res.rule]);
      setSummary(res.summary);
      setPrefix(""); setBaseUrl("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add rule");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!tenantId) return;
    await deleteBaseUrlRule(tenantId, runId, id);
    setRules((prev) => prev.filter((r) => r.id !== id));
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>Base URL</h3>
      <p className="muted">Prepend a base to relative endpoints whose path is missing it (cross-file base URL).</p>
      <div>
        <label htmlFor="base-prefix">Path prefix</label>
        <input id="base-prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)}
          placeholder="/address" />
      </div>
      <div>
        <label htmlFor="base-url">Base URL</label>
        <input id="base-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="/location or https://api.example.com/v3" />
      </div>
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>{busy ? "Adding…" : "Add rule"}</button>
      {summary && (
        <p className="muted">documented {summary.documented} · shadow {summary.shadow} · unresolved {summary.unresolved}</p>
      )}
      <ul>
        {rules.map((r) => (
          <li key={r.id}>
            <code>{r.path_prefix}</code> → <code>{r.base_url}</code>
            <button type="button" onClick={() => remove(r.id)} aria-label={`Delete rule ${r.path_prefix}`}>Delete</button>
          </li>
        ))}
      </ul>
    </form>
  );
}
