import type { FindingsResponse, Finding } from "../../api/types";
import { FindingDetail } from "./FindingDetail";

function groupByType(findings: Finding[]): Record<string, Finding[]> {
  const out: Record<string, Finding[]> = {};
  for (const f of findings) (out[f.type] ??= []).push(f);
  return out;
}

export function FindingsView({ data, runId }: { data: FindingsResponse; runId: string }) {
  const groups = groupByType(data.findings);
  const c = data.coverage;
  return (
    <div>
      <div className="card">
        <h3>Coverage</h3>
        {c ? (
          <p className="muted">
            attributed {c.attributed} · unattributed {c.unattributed} · secrets {c.secrets}
            {c.secrets_engine ? ` (${c.secrets_engine})` : ""} · sources {c.sources_recovered}
          </p>
        ) : <p className="muted">Coverage not available yet.</p>}
      </div>
      {Object.entries(groups).map(([type, items]) => (
        <section key={type}>
          <h3>{type} <span className="muted">({items.length})</span></h3>
          {items.map((f) => <FindingDetail key={f.finding_hash} finding={f} runId={runId} />)}
        </section>
      ))}
    </div>
  );
}
