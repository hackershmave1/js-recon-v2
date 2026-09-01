import { useEffect, useMemo, useState } from "react";
import type { FindingsResponse, Finding, SourceJump } from "../../api/types";
import { typeLabel } from "../../api/findingLabels";
import { getFindings } from "../../api/apiClient";
import { useTenant } from "../../tenant/TenantContext";
import { FindingDrawer } from "./FindingDrawer";
import { SpecUpload } from "./SpecUpload";
import { BaseUrlPanel } from "./BaseUrlPanel";
import { WrapperPanel } from "./WrapperPanel";
import { useResizableRail } from "../../shell/useResizableRail";
import { Icon } from "../../shell/icons";
import "./findings.css";

// Real-field facets only. The design also offers Severity / Scope / Detection-engine /
// Confidence filters; those are omitted because production findings carry
// severity = null and the backend doesn't classify scope/engine-plurality yet.
type FacetKey = "type" | "class" | "triage" | "host";
const FACET_KEYS: FacetKey[] = ["type", "class", "triage", "host"];
const FACET_LABELS: Record<FacetKey, string> = {
  type: "Type", class: "Classification", triage: "Triage", host: "Host",
};

function facetValues(f: Finding, key: FacetKey): string[] {
  switch (key) {
    case "type": return [f.type];
    // null spec_status -> "unclassified" (no spec attached, or not an endpoint).
    case "class": return [f.spec_status?.status ?? "unclassified"];
    case "triage": return [f.triage?.status ?? "untriaged"];
    // multi-valued: a finding matches the Host facet if ANY sighting is on a chosen host.
    case "host": return f.occurrences.map((o) => o.host).filter((h): h is string => !!h);
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

export function FindingsPage({ data, runId, onJumpToSource }: {
  data: FindingsResponse; runId: string; onJumpToSource: (j: SourceJump) => void;
}) {
  const [sel, setSel] = useState<Record<string, Set<string>>>({});
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Finding | null>(null);
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

  const facets = useMemo(() =>
    FACET_KEYS.map((key) => {
      const counts = new Map<string, number>();
      for (const f of view.findings) for (const v of facetValues(f, key)) counts.set(v, (counts.get(v) ?? 0) + 1);
      const options = [...counts.entries()].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count);
      return { key, label: FACET_LABELS[key], options };
    }).filter((facet) => facet.options.length > 0),
  [view.findings]);

  const visible = useMemo(() => view.findings.filter((f) => {
    for (const key of FACET_KEYS) {
      const chosen = sel[key];
      if (chosen && chosen.size > 0 && !facetValues(f, key).some((v) => chosen.has(v))) return false;
    }
    return matchesQuery(f, query);
  }), [view.findings, sel, query]);

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
              {visible.map((f) => {
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
                  <li key={f.finding_hash}>
                    <button type="button"
                      className={"fp-rowbtn" + (selected?.finding_hash === f.finding_hash ? " sel" : "")}
                      onClick={() => setSelected(f)}>
                      <span className="fp-row-top">
                        <span className={`fp-type fp-type-${f.type}`}>{typeLabel(f.type)}</span>
                        <span className={`chip chip-${cls}`}>{cls}</span>
                        <span className="fp-val">{f.value ?? f.path ?? "(unnamed)"}</span>
                        {host && <span className="fp-host">{host}</span>}
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
