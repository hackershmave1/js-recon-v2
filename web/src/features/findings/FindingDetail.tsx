import type { Finding } from "../../api/types";
import { TriageControls } from "./TriageControls";
import { RevealButton } from "./RevealButton";

export function FindingDetail({ finding, runId }: { finding: Finding; runId: string }) {
  const isSecret = finding.type === "secret";
  return (
    <div className="card">
      <div>
        <strong className={finding.severity === "high" ? "sev-high" : ""}>{finding.type}</strong>{" "}
        <span className="muted">{finding.path ?? finding.value ?? ""}</span>
      </div>
      <ul>
        {finding.occurrences.map((o, i) => (
          <li key={i} className="muted">
            {o.source_path ?? o.host ?? "?"}{o.line != null ? `:${o.line}` : ""}
            {/* Slice Y: which discovered asset this sighting came from; absent
                (null) for legacy pre-crawl occurrences, so nothing renders. */}
            {o.asset_url ? ` · ${o.asset_url}` : ""}
            {/* evidence is server-redacted for secrets; render only when present */}
            {o.evidence && !isSecret ? ` — ${o.evidence}` : ""}
            {o.engine ? ` [${o.engine}]` : ""}
          </li>
        ))}
      </ul>
      {isSecret && finding.revealable && <RevealButton runId={runId} hash={finding.finding_hash} />}
      <TriageControls runId={runId} hash={finding.finding_hash} current={finding.triage?.status ?? "open"} />
    </div>
  );
}
