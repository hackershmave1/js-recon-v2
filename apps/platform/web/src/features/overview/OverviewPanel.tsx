import { useNavigate, useParams } from "react-router";
import type { FindingsResponse, Finding, HostsResponse, TechnologiesResponse } from "../../api/types";
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
  // D33-B: a suspected secret ranks below a confirmed one but above generic endpoints —
  // still credential-shaped, just lower-confidence.
  if (f.type === "secret_suspected") return 2;
  // Cleartext internal-IP disclosure: a security-relevant info leak, ranked just below the
  // credential-shaped secrets lanes and above generic endpoints.
  if (f.type === "internal_ip") return 3;
  if (f.type === "endpoint") return 4;
  return 5;
}

export function OverviewPanel(
  { data, technologies, hosts }: {
    data: FindingsResponse; technologies?: TechnologiesResponse | null; hosts?: HostsResponse | null;
  },
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
  // D33-B: the opt-in recall count, surfaced on the Secrets card so an operator who
  // turned the lane on sees it (distinct from the precision `secrets` headline value).
  const suspectedSecrets = countType(data.findings, "secret_suspected");
  // Cleartext internal-IP info-disclosure: no coverage field exists for it, so count it
  // client-side from the findings list (like the suspected-secret tally above).
  const internalIps = countType(data.findings, "internal_ip");
  const files = c ? c.files.length : null;
  // `count` is the FLAT total of technologies across every host (not a host count).
  const techCount = technologies ? technologies.count : null;
  const techTop = technologies
    ? Object.values(technologies.hosts).flat().slice(0, 3).map((t) => t.name).join(", ")
    : null;
  const hostCount = hosts ? hosts.count : null;
  const hostsOut = hosts ? hosts.count - hosts.in_scope : null;

  const metrics = [
    { key: "files", label: "Files", section: "sources",
      value: files == null ? DASH : String(files),
      sub: c ? `${c.sources_recovered} via source maps` : "awaiting analysis" },
    { key: "endpoints", label: "Endpoints", section: "findings",
      value: String(endpoints),
      sub: data.spec ? `${data.spec.shadow} shadow` : "API surface" },
    { key: "hosts", label: "Hosts", section: "hosts",
      value: hostCount == null ? DASH : String(hostCount),
      sub: hosts ? `${hosts.in_scope} in scope · ${hostsOut} out` : "attack surface" },
    { key: "secrets", label: "Secrets", section: "findings",
      value: String(secrets),
      sub: suspectedSecrets > 0
        ? `+${suspectedSecrets} suspected (low-confidence)`
        : c?.secrets_engine ? `secrets engine ${c.secrets_engine}` : "hardcoded values" },
    { key: "internal_ips", label: "Internal IPs", section: "findings",
      value: String(internalIps),
      sub: "cleartext IP disclosure" },
    { key: "coverage", label: "Attribution", section: "findings",
      value: attributionPct == null ? DASH : `${attributionPct}%`,
      sub: c ? `${c.attributed} attributed · ${c.unattributed} not` : "awaiting analysis" },
    { key: "tech", label: "Tech stack", section: "tech",
      value: techCount == null ? DASH : String(techCount),
      sub: techTop || "server · framework · libs" },
  ];

  const top = [...data.findings].sort((a, b) => priorityRank(a) - priorityRank(b)).slice(0, 6);

  // Coverage-gap notes shown under ONE "Partial" banner — a run can be both curtailed AND
  // have a skipped map, so collect the reasons rather than stacking two identical chips.
  const partialNotes: string[] = [];
  if (c?.curtailed) {
    partialNotes.push(
      "Extraction hit the analyzer's size budget on a very large bundle — some endpoints and hosts may be missing.",
    );
  }
  if (c?.source_map === "skipped") {
    partialNotes.push(
      "A referenced source map couldn't be fetched (too large or unavailable) — recovered original sources, and any secrets in them, may be incomplete.",
    );
  }

  return (
    <div className="ov">
      {partialNotes.length > 0 && (
        <div className="ov-curtailed" role="status">
          <span className="ov-curtailed-tag">Partial</span>
          <span>{partialNotes.join(" ")}</span>
        </div>
      )}
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
