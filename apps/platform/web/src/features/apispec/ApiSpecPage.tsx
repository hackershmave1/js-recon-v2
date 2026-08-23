import { useEffect, useMemo, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { getRequests, ApiError } from "../../api/apiClient";
import type {
  Finding, FindingsResponse, ReconstructedRequest, RequestsResponse, SpecStatus,
} from "../../api/types";
import { ExportSpecButton } from "../export/ExportSpecButton";
import { SpecUpload } from "../findings/SpecUpload";
import { FindingDrawer } from "../findings/FindingDrawer";
import { useResizableRail } from "../../shell/useResizableRail";
import { Icon } from "../../shell/icons";
import "./apispec.css";

type SpecClass = SpecStatus["status"] | "unclassified";

// One reconstructed operation, joined to the endpoint finding(s) it came from so
// the detail can show the spec verdict, source location, and a drawer link.
interface OpView {
  req: ReconstructedRequest;
  tag: string;
  cls: SpecClass;
  linked: Finding[];
  trace: { path: string; line: number | null } | null;
}

const STANDARD_METHODS = new Set(["get", "post", "put", "patch", "delete", "head", "options"]);

function methodClass(method: string): string {
  const m = method.toLowerCase();
  if (m === "get") return "as-m-get";
  if (m === "post") return "as-m-post";
  if (m === "put" || m === "patch") return "as-m-put";
  if (m === "delete") return "as-m-delete";
  // websocket + any non-standard verb share the "other" colour
  return STANDARD_METHODS.has(m) ? "as-m-other" : "as-m-websocket";
}

// Group key = first path segment, mirroring the design's "/tag" headers. Bare "/"
// and query-only paths fall back to "(root)".
function pathTag(path: string): string {
  return path.split("/").filter(Boolean)[0] ?? "(root)";
}

function toOps(requests: ReconstructedRequest[], findings: Finding[]): OpView[] {
  const byHash = new Map(findings.map((f) => [f.finding_hash, f]));
  return requests.map((req) => {
    const linked = req.endpoint_hashes
      .map((h) => byHash.get(h))
      .filter((f): f is Finding => f !== undefined);
    const cls: SpecClass = linked.find((f) => f.spec_status)?.spec_status?.status ?? "unclassified";
    let trace: OpView["trace"] = null;
    for (const f of linked) {
      const o = f.occurrences.find((oc) => oc.source_path);
      if (o) { trace = { path: o.source_path as string, line: o.line }; break; }
    }
    return { req, tag: pathTag(req.path), cls, linked, trace };
  });
}

function OperationDetail({ op, onOpenFinding }: {
  op: OpView; onOpenFinding: (f: Finding) => void;
}) {
  const { req, cls, trace, linked } = op;
  const params = [
    ...req.query_params.map((q) => ({ name: q.name, loc: "query" as const, example: q.example })),
    ...req.body_params.map((b) => ({ name: b, loc: "body" as const, example: null as string | null })),
  ];
  const finding = linked[0] ?? null;
  return (
    <div className="as-detail-body">
      <div className="as-op-title">
        <span className={`as-op-method ${methodClass(req.method)}`}>{req.method}</span>
        <span className="as-op-fullpath">{req.path}</span>
        <span className={`chip chip-${cls}`}>{cls}</span>
      </div>
      <div className="as-op-sub">
        {req.hosts.length > 0 && <span className="as-op-host">{req.hosts.join(", ")}</span>}
        <span className="as-op-probe">{req.probeable ? "probeable" : "not probeable"}</span>
        {req.content_type && <span className="as-op-probe">· {req.content_type}</span>}
      </div>

      <div className="as-section-label">Parameters</div>
      {params.length > 0 ? (
        <div className="as-params">
          {params.map((p) => (
            <div key={`${p.loc}:${p.name}`} className="as-param">
              <span className="as-param-name">{p.name}</span>
              <span className={`as-param-loc as-param-loc-${p.loc}`}>{p.loc}</span>
              {p.example != null && <span className="as-param-ex">e.g. {p.example}</span>}
            </div>
          ))}
        </div>
      ) : (
        <div className="as-none">No parameters observed for this operation.</div>
      )}

      <div className="as-links">
        {trace && (
          <div className="as-linkbox">
            <span>
              <span className="as-linkbox-cap">TRACE TO SOURCE</span>
              <span className="as-linkbox-val">{trace.path}{trace.line != null ? `:${trace.line}` : ""}</span>
            </span>
          </div>
        )}
        {finding && (
          <button type="button" className="as-linkbox" onClick={() => onOpenFinding(finding)}>
            <span>
              <span className="as-linkbox-cap">LINKED FINDING</span>
              <span className="as-linkbox-val as-linkbox-finding">
                {finding.type}: {finding.value ?? finding.path ?? "(unnamed)"}
              </span>
            </span>
          </button>
        )}
      </div>

      <div className="as-note">
        <span className="as-note-text">
          Operations reflect only what was reconstructed from the client JavaScript.
          Authentication requirements and vulnerabilities will be assessed by the planned Threat Model (coming soon).
        </span>
      </div>
    </div>
  );
}

export function ApiSpecPage({ data, runId }: { data: FindingsResponse | null; runId: string }) {
  const { tenantId } = useTenant();
  const [requests, setRequests] = useState<RequestsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selOp, setSelOp] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [drawer, setDrawer] = useState<Finding | null>(null);
  const { width: railWidth, collapsed: railCollapsed, toggleCollapsed: toggleRail, resizerProps } = useResizableRail("apispec");

  useEffect(() => {
    if (!tenantId) return;
    let live = true;
    getRequests(tenantId, runId)
      .then((d) => { if (live) setRequests(d); })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e.message : "Failed to load operations"); });
    return () => { live = false; };
  }, [tenantId, runId]);

  const ops = useMemo(
    () => (requests ? toOps(requests.requests, data?.findings ?? []) : []),
    [requests, data],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? ops.filter((o) => o.req.operation.toLowerCase().includes(q)) : ops;
  }, [ops, query]);

  const groups = useMemo(() => {
    const byTag = new Map<string, OpView[]>();
    for (const o of filtered) {
      const list = byTag.get(o.tag) ?? [];
      list.push(o);
      byTag.set(o.tag, list);
    }
    return [...byTag.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([tag, list]) => ({
        tag,
        list: list.sort((a, b) => a.req.operation.localeCompare(b.req.operation)),
      }));
  }, [filtered]);

  // Auto-select the first (filtered) operation so the detail is never blank when
  // there is something to show; falls back cleanly as the filter narrows.
  const current = (selOp && filtered.find((o) => o.req.operation === selOp)) || filtered[0] || null;

  return (
    <div>
      <div className="as">
        <aside className={"as-rail" + (railCollapsed ? " as-rail-collapsed" : "")}
          style={railCollapsed ? undefined : { width: railWidth, flexBasis: railWidth }}>
          <div className="as-rail-head">
            <div className="as-rail-titlerow">
              <h2 className="as-rail-title">API Spec <span className="as-badge">vespasian</span></h2>
              <button type="button" className="as-rail-toggle" onClick={toggleRail}
                title="Collapse operations" aria-label="Collapse operations panel"><Icon name="panel" size={15} /></button>
            </div>
            <div className="as-rail-sub">
              Reconstructed statically from client JS{requests ? ` · ${requests.count} operations` : ""}
            </div>
          </div>
          <div className="as-search">
            <input value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter operations…" aria-label="Filter operations" />
          </div>
          <div className="as-oplist">
            {groups.map((g) => (
              <div key={g.tag}>
                <div className="as-group-label">/{g.tag}</div>
                {g.list.map((o) => (
                  <button key={o.req.operation} type="button"
                    className={"as-op" + (current?.req.operation === o.req.operation ? " sel" : "")}
                    onClick={() => setSelOp(o.req.operation)}>
                    <span className={`as-method ${methodClass(o.req.method)}`}>{o.req.method}</span>
                    <span className="as-op-path">{o.req.path}</span>
                    {o.cls === "shadow" && <span className="as-dot as-dot-shadow" title="shadow (undocumented)" />}
                    {o.cls === "unresolved" && <span className="as-dot as-dot-unresolved" title="unresolved" />}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </aside>

        {!railCollapsed && (
          <div className="as-resizer" role="separator" aria-orientation="vertical"
            aria-label="Resize operations panel" title="Drag to resize" {...resizerProps} />
        )}

        <div className="as-detail">
          <div className="as-detail-head">
            {railCollapsed && (
              <button type="button" className="as-rail-toggle" onClick={toggleRail}
                title="Show operations" aria-label="Show operations panel"><Icon name="panel" size={15} /></button>
            )}
            <span className="as-detail-meta">{ops.length} operations · reconstructed</span>
            <span style={{ flex: 1 }} />
            <ExportSpecButton runId={runId} />
          </div>

          {error ? (
            <div className="as-empty">
              <div className="as-empty-title">Couldn't load operations</div>
              <div>{error}</div>
            </div>
          ) : !current ? (
            <div className="as-empty">
              <div className="as-empty-title">Nothing reconstructed</div>
              <div>No HTTP calls were recovered from this run's JavaScript. Attach a reference spec below to compare, or re-run with more sources.</div>
            </div>
          ) : (
            <OperationDetail op={current} onOpenFinding={setDrawer} />
          )}
        </div>
      </div>

      <FindingDrawer finding={drawer} runId={runId} onClose={() => setDrawer(null)} />

      {/* Spec-attach mirrored here (its other home is the Findings tuning block),
          because comparing the reconstructed surface against a reference OpenAPI
          spec is naturally an API-Spec-page action. */}
      <details className="as-attach">
        <summary>Attach a reference spec (shadow-API classification)</summary>
        <SpecUpload runId={runId} initialSummary={data?.spec ?? null} />
      </details>
    </div>
  );
}
