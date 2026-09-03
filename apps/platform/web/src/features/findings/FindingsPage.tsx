import { useEffect, useMemo, useState } from "react";
import type { FindingsResponse, Finding, SourceJump } from "../../api/types";
import { TRIAGE_STATUSES } from "../../api/types";
import { typeLabel } from "../../api/findingLabels";
import { getFindings, triageFinding } from "../../api/apiClient";
import { useTenant } from "../../tenant/TenantContext";
import { FindingDrawer } from "./FindingDrawer";
import { SpecUpload } from "./SpecUpload";
import { BaseUrlPanel } from "./BaseUrlPanel";
import { WrapperPanel } from "./WrapperPanel";
import { useResizableRail } from "../../shell/useResizableRail";
import { Icon } from "../../shell/icons";
import "./findings.css";

// Real-field facets only. Severity/Scope/Detection-engine/Confidence were omitted while findings
// carried severity = null; D49 now derives a priority + surfaces risk tags, so "Risk" is a real
// facet and severity drives the default sort. Scope/engine-plurality remain unclassified.
type FacetKey = "type" | "class" | "triage" | "host" | "risk";
const FACET_KEYS: FacetKey[] = ["type", "class", "triage", "host", "risk"];
const FACET_LABELS: Record<FacetKey, string> = {
  type: "Type", class: "Classification", triage: "Triage", host: "Host", risk: "Risk",
};

// D49: risk tags (auth/admin/idor/flag) ride in `attributes.risk_tags` — pull them out defensively.
function riskTags(f: Finding): string[] {
  const raw = (f.attributes as { risk_tags?: unknown })?.risk_tags;
  return Array.isArray(raw) ? raw.filter((t): t is string => typeof t === "string") : [];
}

function facetValues(f: Finding, key: FacetKey): string[] {
  switch (key) {
    case "type": return [f.type];
    // null spec_status -> "unclassified" (no spec attached, or not an endpoint).
    case "class": return [f.spec_status?.status ?? "unclassified"];
    case "triage": return [f.triage?.status ?? "untriaged"];
    // multi-valued: a finding matches the Host facet if ANY sighting is on a chosen host.
    case "host": return f.occurrences.map((o) => o.host).filter((h): h is string => !!h);
    // multi-valued: a finding matches the Risk facet if it carries ANY chosen risk tag.
    case "risk": return riskTags(f);
  }
}

function matchesQuery(f: Finding, q: string): boolean {
  if (!q) return true;
  const hay = [f.value, f.path, f.spec_status?.matched_operation,
    ...f.occurrences.map((o) => o.source_path), ...f.occurrences.map((o) => o.host)]
    .filter(Boolean).join(" ").toLowerCase();
  return hay.includes(q.toLowerCase());
}

// Slice 4: a compact "also seen: N capture · M platform" label for a finding's
// cross-run sightings, or null when there's nothing to show (all-zero, ungrouped,
// or the field is absent). Zero buckets are dropped so a capture-only dup reads clean.
function sightingsLabel(s: Finding["sightings"]): string | null {
  if (!s) return null;
  const parts: string[] = [];
  if (s.capture > 0) parts.push(`${s.capture} capture`);
  if (s.platform > 0) parts.push(`${s.platform} platform`);
  return parts.length ? `also seen: ${parts.join(" · ")}` : null;
}

// D50: client-side findings export from the already-fetched (filtered) set — no backend endpoint
// (only OpenAPI existed). CSV for triage spreadsheets, JSON for tooling.
function download(name: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.rel = "noopener";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function csvCell(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function findingsToCsv(rows: Finding[]): string {
  const header = ["type", "severity", "priority", "value", "path", "hosts", "risk_tags", "classification", "triage"];
  const lines = [header.join(",")];
  for (const f of rows) {
    const hosts = [...new Set(f.occurrences.map((o) => o.host).filter(Boolean))].join(" ");
    lines.push([
      f.type, f.severity ?? "", f.priority ?? "", f.value ?? f.path ?? "", f.path ?? "",
      hosts, riskTags(f).join(" "), f.spec_status?.status ?? "unclassified", f.triage?.status ?? "untriaged",
    ].map(csvCell).join(","));
  }
  return lines.join("\n");
}

export function FindingsPage({ data, runId, onJumpToSource }: {
  data: FindingsResponse; runId: string; onJumpToSource: (j: SourceJump) => void;
}) {
  const [sel, setSel] = useState<Record<string, Set<string>>>({});
  const [query, setQuery] = useState("");
  // D49: default to priority order so the highest-risk surface is at the top of a big run.
  const [sortBy, setSortBy] = useState<"priority" | "default">("priority");
  const [selected, setSelected] = useState<Finding | null>(null);
  // D50: bulk triage (multi-select + loop the per-finding endpoint) with a local overlay so rows
  // reflect immediately (the page has no refetch callback), and a render cap bounds the DOM.
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [triageOver, setTriageOver] = useState<Record<string, string>>({});
  const [bulkBusy, setBulkBusy] = useState(false);
  const [limit, setLimit] = useState(300);
  const { width: railWidth, collapsed: railCollapsed, toggleCollapsed: toggleRail, resizerProps } = useResizableRail("findings");
  // #3: analytics/telemetry/vendor hosts are hidden by default; this toggle re-fetches the run's
  // findings WITH the noise (include_noise=true) so an operator can audit what was filtered out.
  const { tenantId } = useTenant();
  const [showAnalytics, setShowAnalytics] = useState(false);
  const [withNoise, setWithNoise] = useState<FindingsResponse | null>(null);
  useEffect(() => {
    if (!showAnalytics || !tenantId) { setWithNoise(null); return; }
    let live = true;
    getFindings(tenantId, runId, true).then((r) => { if (live) setWithNoise(r); }).catch(() => {});
    return () => { live = false; };
  }, [showAnalytics, tenantId, runId]);
  const view = showAnalytics && withNoise ? withNoise : data;

  // D50: apply local triage overrides (from bulk actions) so facets + rows reflect them without a
  // refetch. A no-op ({} overrides) returns the source array unchanged.
  const effective = useMemo(() => {
    if (Object.keys(triageOver).length === 0) return view.findings;
    return view.findings.map((f) => {
      const ov = triageOver[f.finding_hash];
      if (!ov) return f;
      return { ...f, triage: { status: ov, note: f.triage?.note ?? null, actor: f.triage?.actor ?? "you", updated_at: f.triage?.updated_at ?? new Date().toISOString() } };
    });
  }, [view.findings, triageOver]);

  const facets = useMemo(() =>
    FACET_KEYS.map((key) => {
      const counts = new Map<string, number>();
      for (const f of effective) for (const v of facetValues(f, key)) counts.set(v, (counts.get(v) ?? 0) + 1);
      const options = [...counts.entries()].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count);
      return { key, label: FACET_LABELS[key], options };
    }).filter((facet) => facet.options.length > 0),
  [effective]);

  const visible = useMemo(() => effective.filter((f) => {
    for (const key of FACET_KEYS) {
      const chosen = sel[key];
      if (chosen && chosen.size > 0 && !facetValues(f, key).some((v) => chosen.has(v))) return false;
    }
    return matchesQuery(f, query);
  }), [effective, sel, query]);

  // D49: priority sort (highest first), stable on ties so the natural order is preserved within a band.
  const sorted = useMemo(() => {
    if (sortBy === "default") return visible;
    return visible
      .map((f, i) => [f, i] as const)
      .sort((a, b) => (b[0].priority ?? 0) - (a[0].priority ?? 0) || a[1] - b[1])
      .map(([f]) => f);
  }, [visible, sortBy]);

  const anyFilter = query !== "" || Object.values(sel).some((s) => s.size > 0);

  // Slice 4 (Option A): when the run's session has no engagement, the backend sends
  // sightings === null on every finding (never a counts object). Surface that as a
  // hint instead of silently showing no badges, so an ungrouped run doesn't read as
  // "no duplicates". `=== null` (not falsy) keeps a pre-slice-4 `undefined` silent.
  const ungrouped = view.findings.length > 0 && view.findings.every((f) => f.sightings === null);

  function toggle(key: string, value: string) {
    setSel((prev) => {
      const next = new Set(prev[key]);
      next.has(value) ? next.delete(value) : next.add(value);
      return { ...prev, [key]: next };
    });
  }
  function clearAll() { setSel({}); setQuery(""); }

  // D50 bulk triage: select rows, then apply one status by looping the per-finding endpoint. A local
  // overlay (triageOver) reflects the result immediately since the page has no refetch callback.
  function toggleCheck(hash: string) {
    setChecked((prev) => {
      const n = new Set(prev);
      if (n.has(hash)) n.delete(hash); else n.add(hash);
      return n;
    });
  }
  async function applyBulkTriage(status: string) {
    if (!tenantId || checked.size === 0 || bulkBusy) return;
    setBulkBusy(true);
    const done: Record<string, string> = {};
    await Promise.all([...checked].map(async (h) => {
      try { await triageFinding(tenantId, runId, h, { status }); done[h] = status; } catch { /* skip a failed row */ }
    }));
    setTriageOver((prev) => ({ ...prev, ...done }));
    setChecked(new Set());
    setBulkBusy(false);
  }
  // D50 export: client-side CSV/JSON of the currently-visible (filtered + sorted) findings.
  function exportCsv() { download(`findings-${runId}.csv`, findingsToCsv(sorted), "text/csv;charset=utf-8"); }
  function exportJson() { download(`findings-${runId}.json`, JSON.stringify(sorted, null, 2), "application/json"); }

  return (
    <div>
      <div className="fp">
        <aside className={"fp-rail" + (railCollapsed ? " fp-rail-collapsed" : "")}
          style={railCollapsed ? undefined : { width: railWidth, flexBasis: railWidth }}>
          <div className="fp-rail-head">
            <h2 className="fp-rail-title">Findings</h2>
            <button type="button" className="fp-rail-toggle" onClick={toggleRail}
              title="Collapse filters" aria-label="Collapse filters panel"><Icon name="panel" size={15} /></button>
          </div>
          <div className="fp-count"><b>{visible.length}</b> of {view.findings.length} shown</div>
          <label className="fp-noise" title="Third-party analytics/telemetry/vendor hosts (amplitude, sentry, stripe, …) are hidden by default">
            <input type="checkbox" checked={showAnalytics}
              onChange={(e) => setShowAnalytics(e.target.checked)} />
            <span>Show analytics hosts</span>
          </label>
          {facets.map((facet) => (
            <div key={facet.key}>
              <div className="fp-facet-label">{facet.label}</div>
              {facet.options.map((o) => {
                const on = !!sel[facet.key]?.has(o.value);
                return (
                  <button key={o.value} type="button" className={"fp-opt" + (on ? " on" : "")}
                    aria-pressed={on} onClick={() => toggle(facet.key, o.value)}>
                    <span className="fp-opt-box">{on ? "✓" : ""}</span>
                    <span className="fp-opt-name">{facet.key === "type" ? typeLabel(o.value) : o.value}</span>
                    <span className="fp-opt-count">{o.count}</span>
                  </button>
                );
              })}
            </div>
          ))}
          {anyFilter && <button type="button" className="fp-clear" onClick={clearAll}>Clear filters</button>}
        </aside>

        {!railCollapsed && (
          <div className="fp-resizer" role="separator" aria-orientation="vertical"
            aria-label="Resize filters panel" title="Drag to resize" {...resizerProps} />
        )}

        <div className="fp-main">
          <div className="fp-search">
            {railCollapsed && (
              <button type="button" className="fp-rail-toggle" onClick={toggleRail}
                title="Show filters" aria-label="Show filters panel"><Icon name="panel" size={15} /></button>
            )}
            <input value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Search value, path, host…" aria-label="Search findings" />
            <select className="fp-sort" value={sortBy}
              onChange={(e) => setSortBy(e.target.value as "priority" | "default")}
              aria-label="Sort findings">
              <option value="priority">Sort: Priority</option>
              <option value="default">Sort: Default</option>
            </select>
          </div>
          <div className="fp-actions">
            {checked.size > 0 ? (
              <div className="fp-bulk" role="group" aria-label="Bulk triage">
                <span className="fp-bulk-count">{checked.size} selected</span>
                <span className="fp-bulk-label">Triage as:</span>
                {TRIAGE_STATUSES.map((s) => (
                  <button key={s} type="button" className="fp-bulk-btn" disabled={bulkBusy}
                    onClick={() => applyBulkTriage(s)}>{s}</button>
                ))}
                <button type="button" className="fp-bulk-clear" onClick={() => setChecked(new Set())}>Clear</button>
              </div>
            ) : (
              <span className="fp-actions-hint">{visible.length} shown</span>
            )}
            <span className="fp-export">
              <span className="fp-export-label">Export:</span>
              <button type="button" onClick={exportCsv} disabled={visible.length === 0}>CSV</button>
              <button type="button" onClick={exportJson} disabled={visible.length === 0}>JSON</button>
            </span>
          </div>
          {ungrouped && (
            <div className="fp-sightings-hint">
              Cross-run sightings are off for this run. Group its session under an engagement
              to see which findings your extension captures and platform crawls share.
            </div>
          )}
          {visible.length === 0 ? (
            <div className="fp-empty">No findings match.</div>
          ) : (
            <ul className="fp-list">
              {sorted.slice(0, limit).map((f) => {
                const cls = f.spec_status?.status ?? "unclassified";
                const host = f.occurrences.find((o) => o.host)?.host;
                const triage = f.triage?.status ?? "untriaged";
                const sight = sightingsLabel(f.sightings);
                // #6: the unconfirmed lane's value is a placeholder (`GET EXPR`, `POST :serverUrl`)
                // — surface the actual call snippet inline so a triager sees where to dig without
                // opening every drawer.
                const evidence = f.type === "endpoint_unresolved"
                  ? f.occurrences.find((o) => o.evidence)?.evidence
                  : null;
                return (
                  <li key={f.finding_hash} className="fp-li">
                    <input type="checkbox" className="fp-check"
                      aria-label={`Select ${f.value ?? f.path ?? f.type}`}
                      checked={checked.has(f.finding_hash)}
                      onChange={() => toggleCheck(f.finding_hash)} />
                    <button type="button"
                      className={"fp-rowbtn" + (selected?.finding_hash === f.finding_hash ? " sel" : "")}
                      onClick={() => setSelected(f)}>
                      <span className="fp-row-top">
                        {f.severity && (
                          <span className={`fp-sev fp-sev-${f.severity}`}
                            title={`Priority ${f.priority ?? 0}/100`}>{f.severity}</span>
                        )}
                        <span className={`fp-type fp-type-${f.type}`}>{typeLabel(f.type)}</span>
                        <span className={`chip chip-${cls}`}>{cls}</span>
                        <span className="fp-val">{f.value ?? f.path ?? "(unnamed)"}</span>
                        {host && <span className="fp-host">{host}</span>}
                        {riskTags(f).map((t) => (
                          <span key={t} className={`fp-risk fp-risk-${t}`}
                            title={`Risk: ${t}`}>{t}</span>
                        ))}
                        <span className="chip">{triage}</span>
                        {sight && (
                          <span className="chip chip-sightings"
                            title="Same finding in other runs of this engagement">{sight}</span>
                        )}
                      </span>
                      {evidence && (
                        <span className="fp-evidence" title="The call site — where to dig deeper">
                          {evidence}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {sorted.length > limit && (
            <button type="button" className="fp-more" onClick={() => setLimit((n) => n + 500)}>
              Show more · {sorted.length - limit} hidden
            </button>
          )}
        </div>
      </div>

      <FindingDrawer finding={selected} runId={runId} onClose={() => setSelected(null)} onJumpToSource={onJumpToSource} />

      {/* Provisional home for the extraction-tuning knobs (spec attach, base-URL
          rules, wrapper teaching) until the R6 per-session Tuning surface. */}
      <details className="fp-tuning">
        <summary>Extraction tuning (advanced)</summary>
        <SpecUpload runId={runId} initialSummary={data.spec} />
        <BaseUrlPanel runId={runId} />
        <WrapperPanel runId={runId} />
      </details>
    </div>
  );
}
