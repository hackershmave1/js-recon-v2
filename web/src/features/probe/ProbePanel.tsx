import { useEffect, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { getRequests, ApiError } from "../../api/apiClient";
import type { ReconstructedRequest, RequestsResponse } from "../../api/types";

function ProbeRequestCard({ req }: { req: ReconstructedRequest }) {
  const [copied, setCopied] = useState<"curl" | "http" | null>(null);
  async function copy(kind: "curl" | "http", text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(kind); setTimeout(() => setCopied(null), 1200);
  }
  return (
    <div className="card">
      <span className="chip">{req.method}</span> <code>{req.path}</code>
      {req.query_params.length > 0 && <p className="muted">query: {req.query_params.map((q) => q.name).join(", ")}</p>}
      {req.body_params.length > 0 && <p className="muted">body: {req.body_params.join(", ")}</p>}
      {req.artifacts ? (
        <div>
          <button type="button" onClick={() => copy("curl", req.artifacts!.curl)}>{copied === "curl" ? "Copied ✓" : "Copy curl"}</button>
          <button type="button" onClick={() => copy("http", req.artifacts!.http)}>{copied === "http" ? "Copied ✓" : "Copy raw-HTTP"}</button>
        </div>
      ) : <p className="muted">not probeable</p>}
    </div>
  );
}

export function ProbePanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [data, setData] = useState<RequestsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getRequests(tenantId, runId)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load requests"));
  }, [tenantId, runId]);

  if (error) return <div className="card"><h3>Manual probe</h3><p className="sev-high">{error}</p></div>;
  if (!data) return null;
  if (data.count === 0) return <div className="card"><h3>Manual probe</h3><p className="muted">No probeable requests reconstructed.</p></div>;
  return (
    <div className="card">
      <h3>Manual probe <span className="muted">({data.count})</span></h3>
      {data.requests.map((r) => <ProbeRequestCard key={r.operation} req={r} />)}
    </div>
  );
}
