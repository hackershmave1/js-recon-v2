import { useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { exportOpenApi, ApiError } from "../../api/apiClient";

export function ExportSpecButton({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [format, setFormat] = useState<"json" | "yaml">("json");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    if (!tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      const blob = await exportOpenApi(tenantId, runId, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `openapi-${runId}.${format}`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiError ? `Couldn't export spec: ${err.message}` : "Couldn't export spec");
    } finally {
      setBusy(false);
    }
  }

  // Inline (not a .card): this control lives in the API Spec page header, so it
  // sits as a small button + format select rather than a standalone panel.
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <button type="button" className="btn-primary" onClick={download} disabled={busy}>{busy ? "Exporting…" : "Export spec"}</button>
      <select value={format} onChange={(e) => setFormat(e.target.value as "json" | "yaml")} aria-label="Export format">
        <option value="json">JSON</option>
        <option value="yaml">YAML</option>
      </select>
      {error && <span className="sev-high"> {error}</span>}
    </span>
  );
}
