import { useEffect, useMemo, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { useRunDataOptional } from "../progress/runData";
import { getRequests, ApiError } from "../../api/apiClient";
import type { ReconstructedRequest, RequestsResponse } from "../../api/types";

// Sentinel for the "Custom host…" <option>; a real host can never collide with it.
const CUSTOM = "__custom__";

// A request is "relative" (host-less) — the case the host-selector resolves — when it
// has no absolute observed URL. example_url is NOT changed by the probe-host override,
// so this predicate stays stable when a host is picked (the selector never vanishes).
function isAbsolute(url: string | null): boolean {
  return !!url && url.includes("://");
}

function hostOf(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    return new URL(raw).host || null;
  } catch {
    return raw || null;
  }
}

function ProbeRequestCard({ req }: { req: ReconstructedRequest }) {
  const [copied, setCopied] = useState<"curl" | "http" | null>(null);
  async function copy(kind: "curl" | "http", text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(kind); setTimeout(() => setCopied(null), 1200);
  }
  return (
    <div className="card">
      <span className="chip">{req.method}</span> <code>{req.path}</code>
      {req.query_params.length > 0 && <p className="muted">query: {req.query_params.map((q) => q.name).join(", ")}</p>}
      {req.body_params.length > 0 && <p className="muted">body: {req.body_params.join(", ")}</p>}
      {req.artifacts ? (
        <div>
          <button type="button" onClick={() => copy("curl", req.artifacts!.curl)}>{copied === "curl" ? "Copied ✓" : "Copy curl"}</button>
          <button type="button" onClick={() => copy("http", req.artifacts!.http)}>{copied === "http" ? "Copied ✓" : "Copy raw-HTTP"}</button>
        </div>
      ) : <p className="muted">not probeable</p>}
    </div>
  );
}

// The host-selector for resolving relative endpoints at probe time (Starbucks QA #2).
// Candidates are the run's IN-SCOPE discovered hosts (from the run-data context — a
// best-effort load, so it may be absent), with the crawl target first; any other host
// is reachable via "Custom host…". The chosen host is sent to the server, which
// re-serializes the curl/raw-HTTP through its hardened path (never assembled here).
function HostSelector({
  hosts, target, useCustom, host, customHost, onPick, onCustom, onCommit,
}: {
  hosts: string[]; target: string | null; useCustom: boolean; host: string;
  customHost: string; onPick: (value: string) => void; onCustom: (value: string) => void;
  onCommit: () => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", margin: "0 0 12px" }}>
      <label htmlFor="probe-host" className="muted">Resolve relative paths against</label>
      <select id="probe-host" value={useCustom ? CUSTOM : host} onChange={(e) => onPick(e.target.value)}>
        {hosts.length === 0 && <option value="">{"{{base_url}} (unresolved)"}</option>}
        {hosts.map((h) => (
          <option key={h} value={h}>{h === target ? `${h} (primary, in scope)` : `${h} (in scope)`}</option>
        ))}
        <option value={CUSTOM}>Custom host…</option>
      </select>
      {useCustom && (
        <input
          type="text" aria-label="Custom host" placeholder="api.example.com"
          value={customHost} onChange={(e) => onCustom(e.target.value)}
          onBlur={onCommit}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onCommit(); } }}
        />
      )}
    </div>
  );
}

export function ProbePanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const runData = useRunDataOptional();

  // In-scope discovered hosts, crawl target first. getHosts/getAssets are best-effort
  // (loaded after the run is "ready"), so both may be null — degrade to "Custom host…".
  const target = hostOf(runData?.assets?.domain);
  const inScopeHosts = useMemo(() => {
    const names = (runData?.hosts?.hosts ?? []).filter((h) => h.in_scope).map((h) => h.host);
    const uniq = Array.from(new Set(names));
    uniq.sort((a, b) => (a === target ? -1 : b === target ? 1 : a.localeCompare(b)));
    return uniq;
  }, [runData?.hosts, target]);

  const [host, setHost] = useState<string>(""); // explicit pick; "" = fall back to the default
  const [useCustom, setUseCustom] = useState(false);
  const [customHost, setCustomHost] = useState(""); // live text of the "Custom host…" field
  const [appliedCustom, setAppliedCustom] = useState(""); // committed on blur/Enter — avoids a re-fetch per keystroke
  const [data, setData] = useState<RequestsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Default to the primary in-scope host without a state write (avoids a controlled-
  // select value with no matching option before the async host list arrives).
  const selected = host || (inScopeHosts[0] ?? "");
  const effectiveHost = useCustom ? appliedCustom : selected;

  useEffect(() => {
    if (!tenantId) return;
    let live = true;
    getRequests(tenantId, runId, effectiveHost || undefined)
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e.message : "Failed to load requests"); });
    return () => { live = false; };
  }, [tenantId, runId, effectiveHost]);

  function onPick(value: string) {
    if (value === CUSTOM) { setUseCustom(true); return; }
    setUseCustom(false);
    setHost(value);
  }

  if (error) return <div className="card"><h3>Manual probe</h3><p className="sev-high">{error}</p></div>;
  if (!data) return null;
  if (data.count === 0) return <div className="card"><h3>Manual probe</h3><p className="muted">No probeable requests reconstructed.</p></div>;

  const hasRelative = data.requests.some((r) => r.probeable && !isAbsolute(r.example_url));

  return (
    <div className="card">
      <h3>Manual probe <span className="muted">({data.count})</span></h3>
      {hasRelative && (
        <HostSelector
          hosts={inScopeHosts} target={target} useCustom={useCustom}
          host={selected} customHost={customHost} onPick={onPick} onCustom={setCustomHost}
          onCommit={() => setAppliedCustom(customHost.trim())}
        />
      )}
      {data.requests.map((r) => <ProbeRequestCard key={r.operation} req={r} />)}
    </div>
  );
}
