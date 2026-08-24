import type { Finding, Occurrence, SourceJump } from "../../api/types";
import { typeLabel } from "../../api/findingLabels";
import { TriageControls } from "./TriageControls";
import { RevealButton } from "./RevealButton";

// The bundle-wide placeholder the analyze stage assigns when no source map recovered
// real per-file paths (backend recon.findings.analyze._SOURCE_NAME). It is NOT a real
// file, so it must never be shown as an occurrence's source — the actual bundle the
// sighting came from is carried on `asset_url` (Slice Y), and a source-map-recovered
// original path (when present) is better still. Keep in sync with the backend constant.
const BUNDLE_FALLBACK = "input.js";

// The occurrence's real source for display: a source-map-recovered original path wins;
// otherwise the actual bundle URL it was sighted in; the "input.js" placeholder is only
// ever a last resort (a legacy single-blob upload that has no asset_url). `bundle` is the
// owning asset shown as a secondary tag ONLY when the primary is a distinct recovered
// path — otherwise the bundle already IS the primary and repeating it is noise.
function occSource(o: Occurrence): { primary: string; bundle: string | null } {
  const recovered = o.source_path && o.source_path !== BUNDLE_FALLBACK ? o.source_path : null;
  const primary = recovered ?? o.asset_url ?? o.source_path ?? o.host ?? "?";
  return { primary, bundle: recovered && o.asset_url ? o.asset_url : null };
}

// `onJumpToSource` is optional (the shared ApiSpec drawer omits it): when present,
// an occurrence with a source location becomes a button that reveals it in Sources.
export function FindingDetail({ finding, runId, onJumpToSource }: {
  finding: Finding; runId: string; onJumpToSource?: (j: SourceJump) => void;
}) {
  // D33-B: the suspected tier reuses the SECRET machinery (server-redacted evidence +
  // the audited reveal), so it takes the same treatment here — a raw suspected value is
  // never rendered, and its reveal button shows when the backend marks it revealable.
  const isSecret = finding.type === "secret" || finding.type === "secret_suspected";
  // null -> never classified (no spec attached to the session, or this
  // finding isn't an endpoint) -- rendered as its own "unclassified" verdict,
  // distinct from the three real classify_operation outcomes.
  const specStatus = finding.spec_status?.status ?? "unclassified";
  // Header identity: the finding's own value (the endpoint/param/secret), falling back
  // to its real source — never the "input.js" placeholder that `finding.path` carries.
  const headerLabel =
    finding.value ?? [...new Set(finding.occurrences.map((o) => occSource(o).primary))][0] ?? "";
  return (
    <div className="card">
      <div>
        <strong className={finding.severity === "high" ? "sev-high" : ""}>{typeLabel(finding.type)}</strong>{" "}
        <span className="muted">{headerLabel}</span>{" "}
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
          const { primary, bundle } = occSource(o);
          // Same rendered text whether or not it's clickable — kept in one place.
          const text = <>
            {primary}{o.line != null ? `:${o.line}` : ""}
            {/* Owning bundle, shown only alongside a distinct recovered path. */}
            {bundle ? ` · ${bundle}` : ""}
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
                  aria-label={`Open ${primary} in Sources`}
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
