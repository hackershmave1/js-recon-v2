import { useEffect, useState } from "react";
import type { DiffEntry, DiffResponse } from "../../api/types";
import { getRunDiff } from "../../api/apiClient";
import { typeLabel } from "../../api/findingLabels";
import { useTenant } from "../../tenant/TenantContext";
import "./findings.css";

function DiffSection({ title, entries, className }: { title: string; entries: DiffEntry[]; className: string }) {
  if (entries.length === 0) return null;
  return (
    <section className={`diff-section diff-${className}`}>
      <h2 className="diff-section-title">
        {title} <span className="diff-count">{entries.length}</span>
      </h2>
      <ul className="diff-list">
        {entries.map((e) => (
          <li key={e.finding_hash} className="diff-li">
            {e.severity && (
              <span className={`fp-sev fp-sev-${e.severity}`}
                title={`Priority ${e.priority}/100`}>{e.severity}</span>
            )}
            <span className={`fp-type fp-type-${e.type}`}>{typeLabel(e.type)}</span>
            <span className="diff-val">{e.value ?? e.path ?? "(unnamed)"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function DiffPage({ runId, baseRunId }: { runId: string; baseRunId: string }) {
  const { tenantId } = useTenant();
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    setDiff(null);
    setError(null);
    getRunDiff(tenantId, runId, baseRunId)
      .then(setDiff)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load diff"));
  }, [tenantId, runId, baseRunId]);

  if (error) return <div className="card"><p className="muted">{error}</p></div>;
  if (!diff) return <div className="card"><p className="muted">Loading diff…</p></div>;

  const total = diff.new.length + diff.persisted.length + diff.gone.length;
  return (
    <div className="diff-page">
      <div className="diff-header">
        <h1 className="diff-title">Run diff</h1>
        <div className="diff-meta muted">
          <span>Comparing <code>{runId.slice(0, 8)}</code> vs base <code>{baseRunId.slice(0, 8)}</code></span>
          <span>{total} findings across both runs</span>
        </div>
        {diff.base_incomplete && (
          <div className="diff-warning">
            The base run is incomplete (partial / failed / cancelled). Findings in the
            "Gone" section may have been missed by the base run — not necessarily fixed.
          </div>
        )}
      </div>

      {total === 0 && (
        <div className="card"><p className="muted">Both runs have no findings.</p></div>
      )}

      <DiffSection title="New" entries={diff.new} className="new" />
      <DiffSection title="Persisted" entries={diff.persisted} className="persisted" />
      <DiffSection title="Gone" entries={diff.gone} className="gone" />
    </div>
  );
}
