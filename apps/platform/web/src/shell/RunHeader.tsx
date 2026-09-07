import { useNavigate, useParams } from "react-router";
import { useRunData } from "../features/progress/runData";
import {
  computeAttributionPct,
  computeEndpoints,
  computeSecrets,
  computePartialNotes,
} from "../features/overview/metrics";
import "./runHeader.css";

const DASH = "—";
const CIRCUM = 113.1; // 2π × r=18

function stateChipClass(state: string): string {
  if (state === "done") return "rh-chip rh-chip-ok";
  if (state === "failed") return "rh-chip rh-chip-bad";
  if (state === "partial" || state === "paused") return "rh-chip rh-chip-warn";
  if (state === "queued" || state === "cancelled") return "rh-chip rh-chip-muted";
  return "rh-chip rh-chip-run"; // running / "…" / any active stage
}

// onOpenTuning is wired in Slice 2 (REQ-TU3) when TuningRailContext is added.
// The nudge renders now; the handler arrives when the rail exists.
export function RunHeader({ onOpenTuning }: { onOpenTuning?: () => void }) {
  const navigate = useNavigate();
  const { id } = useParams();
  const { runId, findings, hosts, assets, state } = useRunData();

  const c = findings?.coverage ?? null;
  const hostRows = hosts?.hosts ?? [];
  const attributionPct = computeAttributionPct(c);
  const endpoints = findings ? computeEndpoints(findings.findings, hostRows) : null;
  const secrets = findings ? computeSecrets(c, findings.findings) : null;
  const hostCount = hosts?.count ?? null;
  const shadow = findings?.spec?.shadow ?? null;
  const partialNotes = computePartialNotes(c);
  const showNudge = partialNotes.length > 0 || (c !== null && c.unattributed > 0);

  const domain = assets?.domain ?? null;
  const shortId = (id ?? runId).slice(0, 8);
  const files = c?.files.length ?? null;
  const dashOffset = attributionPct != null ? (1 - attributionPct / 100) * CIRCUM : CIRCUM;

  function nudgeText(): string {
    if (c && c.unattributed > 0) {
      return `${c.unattributed} calls unattributed`;
    }
    return "Coverage may be incomplete";
  }

  return (
    <div className="rh" role="banner" aria-label="Run coverage summary">
      <div className="rh-identity">
        <span className="rh-host">{domain ?? "Current run"}</span>
        <span className="rh-meta">
          <span className={stateChipClass(state)}>{state}</span>
          <span className="rh-runid">{shortId}</span>
          {files != null && <span className="rh-files">{files} files</span>}
        </span>
      </div>

      <div className="rh-divider" aria-hidden="true" />

      <div className="rh-attribution">
        <div className="rh-ring" aria-hidden="true">
          <svg width="44" height="44" viewBox="0 0 44 44" style={{ transform: "rotate(-90deg)" }}>
            <circle cx="22" cy="22" r="18" fill="none" stroke="var(--surface)" strokeWidth="4" />
            <circle
              cx="22" cy="22" r="18" fill="none"
              stroke="var(--accent)" strokeWidth="4" strokeLinecap="round"
              strokeDasharray={CIRCUM}
              strokeDashoffset={dashOffset}
            />
          </svg>
          <span className="rh-ring-pct">{attributionPct != null ? `${attributionPct}%` : DASH}</span>
        </div>
        <div className="rh-attr-body">
          <span className="rh-attr-label">Attribution</span>
          <span className="rh-attr-sub">
            {c ? `${c.attributed} attributed · ${c.unattributed} not` : DASH}
          </span>
        </div>
      </div>

      <div className="rh-pills">
        <button
          type="button" className="rh-pill" aria-label="View endpoints"
          onClick={() => navigate(`/runs/${id}/findings`)}
        >
          <span className="rh-pill-val">{endpoints ?? DASH}</span>
          <span className="rh-pill-lbl">endpoints</span>
        </button>
        <button
          type="button" className="rh-pill rh-pill-secrets" aria-label="View secrets"
          onClick={() => navigate(`/runs/${id}/findings`)}
        >
          <span className="rh-pill-val">{secrets ?? DASH}</span>
          <span className="rh-pill-lbl">secrets</span>
        </button>
        <button
          type="button" className="rh-pill" aria-label="View hosts"
          onClick={() => navigate(`/runs/${id}/hosts`)}
        >
          <span className="rh-pill-val">{hostCount ?? DASH}</span>
          <span className="rh-pill-lbl">hosts</span>
        </button>
        <button
          type="button" className="rh-pill rh-pill-shadow" aria-label="View shadow endpoints"
          onClick={() => navigate(`/runs/${id}/findings`)}
        >
          <span className="rh-pill-val">{shadow ?? DASH}</span>
          <span className="rh-pill-lbl">shadow</span>
        </button>
      </div>

      {showNudge && (
        <button type="button" className="rh-nudge" onClick={onOpenTuning}>
          <span className="rh-nudge-icon" aria-hidden="true">⚠</span>
          <span className="rh-nudge-text">
            {nudgeText()} —{" "}
            <span className="rh-nudge-link">tune extraction ›</span>
          </span>
        </button>
      )}
    </div>
  );
}
