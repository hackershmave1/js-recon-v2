import { useNavigate, useParams } from "react-router";
import type { FindingsResponse, Finding, TechnologiesResponse } from "../../api/types";
import { typeLabel } from "../../api/findingLabels";
import "./overview.css";

const DASH = "—"; // shown when the underlying metric isn't available yet

function countType(findings: Finding[], type: string): number {
  return findings.filter((f) => f.type === type).length;
}

// Production findings carry severity = null, so there is no severity to rank by.
// Priority instead follows recon value: an undocumented (shadow) endpoint first,
// then secrets, then other endpoints, then everything else.
function priorityRank(f: Finding): number {
  if (f.type === "endpoint" && f.spec_status?.status === "shadow") return 0;
  if (f.type === "secret") return 1;
  if (f.type === "endpoint") return 2;
  return 3;
}

export function OverviewPanel(
  { data, technologies }: { data: FindingsResponse; technologies?: TechnologiesResponse | null },
) {
  const navigate = useNavigate();
  const { id } = useParams();
  // Each metric card / "View all" is a shortcut to the matching run subpage route.
  const go = (section: string) => navigate(`/runs/${id}/${section}`);
  const c = data.coverage;
  const attributedTotal = c ? c.attributed + c.unattributed : 0;
  const attributionPct = attributedTotal > 0 ? Math.round((c!.attributed / attributedTotal) * 100) : null;
  const endpoints = countType(data.findings, "endpoint");
  const secrets = c ? c.secrets : countType(data.findings, "secret");
  const files = c ? c.files.length : null;
  // `count` is the FLAT total of technologies across every host (not a host count).
  const techCount = technologies ? technologies.count : null;
  const techTop = technologies
    ? Object.values(technologies.hosts).flat().slice(0, 3).map((t) => t.name).join(", ")
    : null;

  const metrics = [
    { key: "files", label: "Files", section: "sources",
      value: files == null ? DASH : String(files),
      sub: c ? `${c.sources_recovered} via source maps` : "awaiting analysis" },
    { key: "endpoints", label: "Endpoints", section: "findings",
      value: String(endpoints),
      sub: data.spec ? `${data.spec.shadow} shadow` : "API surface" },
    { key: "secrets", label: "Secrets", section: "findings",
      value: String(secrets),
      sub: c?.secrets_engine ? `secrets engine ${c.secrets_engine}` : "hardcoded values" },
    { key: "coverage", label: "Attribution", section: "findings",
      value: attributionPct == null ? DASH : `${attributionPct}%`,
      sub: c ? `${c.attributed} attributed · ${c.unattributed} not` : "awaiting analysis" },
    { key: "tech", label: "Tech stack", section: "tech",
      value: techCount == null ? DASH : String(techCount),
      sub: techTop || "server · framework · libs" },
  ];

  const top = [...data.findings].sort((a, b) => priorityRank(a) - priorityRank(b)).slice(0, 6);

  return (
    <div className="ov">
      <div className="ov-metrics">
        {metrics.map((m) => (
          <button key={m.key} type="button" className="ov-card" onClick={() => go(m.section)}>
            <span className="ov-metric-label">{m.label}</span>
            <span className="ov-metric-value">{m.value}</span>
            <span className="ov-metric-sub">{m.sub}</span>
          </button>
        ))}
      </div>

      <div className="ov-panel">
        <div className="ov-panel-head">
          <span className="ov-panel-title">Top findings</span>
          <button type="button" className="ov-link" onClick={() => go("findings")}>View all</button>
        </div>
        {top.length === 0 ? (
          <p className="muted ov-empty">No findings yet.</p>
        ) : (
          <ul className="ov-list">
            {top.map((f) => {
              const occ = f.occurrences[0];
              const where = occ?.source_path
                ? `${occ.source_path}${occ.line != null ? `:${occ.line}` : ""}`
                : null;
              const isShadow = f.type === "endpoint" && f.spec_status?.status === "shadow";
              return (
                <li key={f.finding_hash} className="ov-row">
                  <span className={`ov-type ov-type-${f.type}`}>{typeLabel(f.type)}</span>
                  {isShadow && <span className="chip chip-shadow">shadow</span>}
                  <span className="ov-val">{f.value ?? f.path ?? "(unnamed)"}</span>
                  {where && <span className="ov-where">{where}</span>}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
