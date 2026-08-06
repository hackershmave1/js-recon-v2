import type { Finding, SourceJump } from "../../api/types";
import { TriageControls } from "./TriageControls";
import { RevealButton } from "./RevealButton";

// `onJumpToSource` is optional (the shared ApiSpec drawer omits it): when present,
// an occurrence with a source location becomes a button that reveals it in Sources.
export function FindingDetail({ finding, runId, onJumpToSource }: {
  finding: Finding; runId: string; onJumpToSource?: (j: SourceJump) => void;
}) {
  const isSecret = finding.type === "secret";
  // null -> never classified (no spec attached to the session, or this
  // finding isn't an endpoint) -- rendered as its own "unclassified" verdict,
  // distinct from the three real classify_operation outcomes.
  const specStatus = finding.spec_status?.status ?? "unclassified";
  return (
    <div className="card">
      <div>
        <strong className={finding.severity === "high" ? "sev-high" : ""}>{finding.type}</strong>{" "}
        <span className="muted">{finding.path ?? finding.value ?? ""}</span>{" "}
        <span className={`chip chip-${specStatus}`}>{specStatus}</span>
        {/* Resolved documented op (post base-URL-rule resolution, REQ-C2) next to
            the finding's own unchanged raw value above -- expected non-churn:
            the finding's `value`/`path` never rewrites, only the comparison does. */}
        {finding.spec_status?.matched_operation && (
          <span className="muted"> → {finding.spec_status.matched_operation}</span>
        )}
      </div>
      <ul>
        {finding.occurrences.map((o, i) => {
          // Same rendered text whether or not it's clickable — kept in one place.
          const text = <>
            {o.source_path ?? o.host ?? "?"}{o.line != null ? `:${o.line}` : ""}
            {/* Slice Y: which discovered asset this sighting came from; absent
                (null) for legacy pre-crawl occurrences, so nothing renders. */}
            {o.asset_url ? ` · ${o.asset_url}` : ""}
            {/* evidence is server-redacted for secrets; render only when present */}
            {o.evidence && !isSecret ? ` — ${o.evidence}` : ""}
            {o.engine ? ` [${o.engine}]` : ""}
          </>;
          // Clickable only when wired AND the occurrence has a source location to
          // open; otherwise it stays plain, non-interactive text.
          const jumpable = onJumpToSource && (o.source_path || o.asset_url);
          return (
            <li key={i} className="muted">
              {jumpable ? (
                <button type="button" className="fd-occ"
                  aria-label={`Open ${o.source_path ?? o.host ?? "source"} in Sources`}
                  onClick={() => onJumpToSource({ sourcePath: o.source_path, assetUrl: o.asset_url, line: o.line })}>
                  {text}
                </button>
              ) : text}
            </li>
          );
        })}
      </ul>
      {isSecret && finding.revealable && <RevealButton runId={runId} hash={finding.finding_hash} />}
      <TriageControls runId={runId} hash={finding.finding_hash} current={finding.triage?.status ?? "open"} />
    </div>
  );
}
