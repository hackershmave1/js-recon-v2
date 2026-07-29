import { useState } from "react";
import type { FindingsResponse, Finding } from "../../api/types";
import { FindingDetail } from "./FindingDetail";
import { SpecUpload } from "./SpecUpload";
import { BaseUrlPanel } from "./BaseUrlPanel";

function groupByType(findings: Finding[]): Record<string, Finding[]> {
  const out: Record<string, Finding[]> = {};
  for (const f of findings) (out[f.type] ??= []).push(f);
  return out;
}

export function FindingsView({ data, runId }: { data: FindingsResponse; runId: string }) {
  const [shadowOnly, setShadowOnly] = useState(false);
  // Client-side only (design §6.4 UI): filters findings already in `data`,
  // no re-fetch -- toggling this never issues a network request.
  const visible = shadowOnly
    ? data.findings.filter((f) => f.spec_status?.status === "shadow")
    : data.findings;
  const groups = groupByType(visible);
  const c = data.coverage;
  return (
    <div>
      <SpecUpload runId={runId} initialSummary={data.spec} />
      <BaseUrlPanel runId={runId} />
      <div className="card">
        <h3>Coverage</h3>
        {c ? (
          <p className="muted">
            attributed {c.attributed} · unattributed {c.unattributed} · secrets {c.secrets}
            {c.secrets_engine ? ` (${c.secrets_engine})` : ""} · sources {c.sources_recovered}
          </p>
        ) : <p className="muted">Coverage not available yet.</p>}
      </div>
      <label>
        <input type="checkbox" checked={shadowOnly} onChange={(e) => setShadowOnly(e.target.checked)} />
        {" "}Shadow only
      </label>
      {Object.entries(groups).map(([type, items]) => (
        <section key={type}>
          <h3>{type} <span className="muted">({items.length})</span></h3>
          {items.map((f) => <FindingDetail key={f.finding_hash} finding={f} runId={runId} />)}
        </section>
      ))}
    </div>
  );
}
