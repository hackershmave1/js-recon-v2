import { useMemo, useState } from "react";
import type { FindingsResponse, Finding } from "../../api/types";
import { FindingDrawer } from "./FindingDrawer";
import { SpecUpload } from "./SpecUpload";
import { BaseUrlPanel } from "./BaseUrlPanel";
import { WrapperPanel } from "./WrapperPanel";
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

export function FindingsPage({ data, runId }: { data: FindingsResponse; runId: string }) {
  const [sel, setSel] = useState<Record<string, Set<string>>>({});
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Finding | null>(null);

  const facets = useMemo(() =>
    FACET_KEYS.map((key) => {
      const counts = new Map<string, number>();
      for (const f of data.findings) for (const v of facetValues(f, key)) counts.set(v, (counts.get(v) ?? 0) + 1);
      const options = [...counts.entries()].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count);
      return { key, label: FACET_LABELS[key], options };
    }).filter((facet) => facet.options.length > 0),
  [data.findings]);

  const visible = useMemo(() => data.findings.filter((f) => {
    for (const key of FACET_KEYS) {
      const chosen = sel[key];
      if (chosen && chosen.size > 0 && !facetValues(f, key).some((v) => chosen.has(v))) return false;
    }
    return matchesQuery(f, query);
  }), [data.findings, sel, query]);

  const anyFilter = query !== "" || Object.values(sel).some((s) => s.size > 0);

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
        <aside className="fp-rail">
          <h2 className="fp-rail-title">Findings</h2>
          <div className="fp-count"><b>{visible.length}</b> of {data.findings.length} shown</div>
          {facets.map((facet) => (
            <div key={facet.key}>
              <div className="fp-facet-label">{facet.label}</div>
              {facet.options.map((o) => {
                const on = !!sel[facet.key]?.has(o.value);
                return (
                  <button key={o.value} type="button" className={"fp-opt" + (on ? " on" : "")}
                    aria-pressed={on} onClick={() => toggle(facet.key, o.value)}>
                    <span className="fp-opt-box">{on ? "✓" : ""}</span>
                    <span className="fp-opt-name">{o.value}</span>
                    <span className="fp-opt-count">{o.count}</span>
                  </button>
                );
              })}
            </div>
          ))}
          {anyFilter && <button type="button" className="fp-clear" onClick={clearAll}>Clear filters</button>}
        </aside>

        <div className="fp-main">
          <div className="fp-search">
            <input value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Search value, path, host…" aria-label="Search findings" />
          </div>
          {visible.length === 0 ? (
            <div className="fp-empty">No findings match.</div>
          ) : (
            <ul className="fp-list">
              {visible.map((f) => {
                const cls = f.spec_status?.status ?? "unclassified";
                const host = f.occurrences.find((o) => o.host)?.host;
                const triage = f.triage?.status ?? "untriaged";
                return (
                  <li key={f.finding_hash}>
                    <button type="button"
                      className={"fp-rowbtn" + (selected?.finding_hash === f.finding_hash ? " sel" : "")}
                      onClick={() => setSelected(f)}>
                      <span className={`fp-type fp-type-${f.type}`}>{f.type}</span>
                      <span className={`chip chip-${cls}`}>{cls}</span>
                      <span className="fp-val">{f.value ?? f.path ?? "(unnamed)"}</span>
                      {host && <span className="fp-host">{host}</span>}
                      <span className="chip">{triage}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <FindingDrawer finding={selected} runId={runId} onClose={() => setSelected(null)} />

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
