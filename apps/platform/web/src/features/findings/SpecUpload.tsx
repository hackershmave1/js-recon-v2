import { useState } from "react";
import type React from "react";
import { useTenant } from "../../tenant/TenantContext";
import { attachSpec, ApiError } from "../../api/apiClient";
import type { SpecSummary } from "../../api/types";

export function SpecUpload(
  { runId, initialSummary = null }: { runId: string; initialSummary?: SpecSummary | null },
) {
  const { tenantId } = useTenant();
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  // Seeded from the run's already-fetched "spec" block (design §6.4) so a spec
  // attached in an earlier session still shows its summary on reload, then
  // overwritten by whatever this control's own POST returns.
  const [summary, setSummary] = useState<SpecSummary | null>(initialSummary);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ready = mode === "file" ? file !== null : text.trim() !== "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId || busy) return;
    if (mode === "file" && !file) return;
    setBusy(true); setError(null);
    try {
      const res = mode === "file" && file
        ? await attachSpec(tenantId, runId, file)
        : await attachSpec(tenantId, runId, text);
      setSummary(res);
    } catch (err) {
      // The router's own messages are already readable ("invalid spec: ...",
      // "run not found") -- no extra status->message mapping needed here.
      setError(err instanceof ApiError ? err.message : "Spec upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>API spec</h3>
      <p className="muted">Attach an OpenAPI/Swagger spec to classify this run's endpoints against it.</p>
      <div role="radiogroup" aria-label="Spec input mode">
        <label><input type="radio" name="spec-mode" value="file" checked={mode === "file"}
          onChange={() => setMode("file")} /> Upload a file</label>
        <label><input type="radio" name="spec-mode" value="paste" checked={mode === "paste"}
          onChange={() => setMode("paste")} /> Paste spec text</label>
      </div>
      {mode === "file" ? (
        <div><label htmlFor="spec-file">Spec file</label>
          <input id="spec-file" type="file" accept=".json,.yaml,.yml,application/json,text/yaml"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
      ) : (
        <div><label htmlFor="spec-text">Spec text</label>
          <textarea id="spec-text" value={text} onChange={(e) => setText(e.target.value)}
            placeholder="paste OpenAPI/Swagger JSON or YAML" /></div>
      )}
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>{busy ? "Attaching…" : "Attach spec"}</button>
      {summary && (
        <p className="muted">documented {summary.documented} · shadow {summary.shadow} · unresolved {summary.unresolved} · suffix-verify {summary.suffix_verify} · base-url incompleteness {summary.base_url_incompleteness_ratio}</p>
      )}
    </form>
  );
}
