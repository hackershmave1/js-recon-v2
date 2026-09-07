import { useMemo, useState } from "react";
import type { ReconstructedRequest } from "../../api/types";

type Lane = "all" | "probeable" | "relative" | "ws";

function isAbsolute(url: string | null): boolean {
  return !!url && url.includes("://");
}

function groupTag(req: ReconstructedRequest): string {
  const m = req.path.match(/\/api\/([^/]+)/);
  if (m?.[1]) return m[1];
  if (req.hosts[0]) return req.hosts[0];
  return "other";
}

function ProbeListRow({
  req, selected, isShadow, onSelect,
}: {
  req: ReconstructedRequest;
  selected: boolean;
  isShadow: boolean;
  onSelect: () => void;
}) {
  const isWS = req.method === "WS" || req.method === "WSS";
  const isRelative = !isAbsolute(req.example_url);
  return (
    <button
      type="button"
      className={
        "pb-list-row"
        + (selected ? " pb-list-row-selected" : "")
        + (!req.probeable ? " pb-list-row-dim" : "")
      }
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
    >
      <span className={"pb-method-badge pb-method-" + req.method.toLowerCase()}>{req.method}</span>
      <span className="pb-list-path">{req.path}</span>
      <span className="pb-list-markers">
        {isRelative && <span className="pb-marker pb-marker-relative" title="Relative URL">rel</span>}
        {isWS && <span className="pb-marker pb-marker-ws" title="WebSocket">WS</span>}
        {isShadow && <span className="pb-marker pb-marker-shadow" title="Shadow endpoint">shadow</span>}
        {!req.probeable && <span className="pb-marker pb-marker-np" title="Not probeable">—</span>}
      </span>
    </button>
  );
}

export function ProbeList({
  requests, selectedOp, shadowHashes, onSelect,
}: {
  requests: ReconstructedRequest[];
  selectedOp: string | null;
  shadowHashes: Set<string>;
  onSelect: (op: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [lane, setLane] = useState<Lane>("all");

  const counts = useMemo(() => ({
    all: requests.length,
    probeable: requests.filter((r) => r.probeable).length,
    relative: requests.filter((r) => !isAbsolute(r.example_url)).length,
    ws: requests.filter((r) => r.method === "WS" || r.method === "WSS").length,
  }), [requests]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return requests.filter((r) => {
      if (lane === "probeable" && !r.probeable) return false;
      if (lane === "relative" && isAbsolute(r.example_url)) return false;
      if (lane === "ws" && r.method !== "WS" && r.method !== "WSS") return false;
      if (!q) return true;
      return (
        r.path.toLowerCase().includes(q)
        || r.method.toLowerCase().includes(q)
        || r.hosts.some((h) => h.toLowerCase().includes(q))
      );
    });
  }, [requests, search, lane]);

  const grouped = useMemo(() => {
    const map = new Map<string, ReconstructedRequest[]>();
    for (const r of filtered) {
      const tag = groupTag(r);
      if (!map.has(tag)) map.set(tag, []);
      map.get(tag)!.push(r);
    }
    return Array.from(map.entries());
  }, [filtered]);

  const LANES: { key: Lane; label: string }[] = [
    { key: "all", label: "All" },
    { key: "probeable", label: "Probeable" },
    { key: "relative", label: "Relative" },
    { key: "ws", label: "WS" },
  ];

  return (
    <div className="pb-list">
      <div className="pb-list-controls">
        <input
          type="search"
          className="pb-search"
          placeholder="Search path, method, host…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search requests"
        />
        <div className="pb-lanes" role="group" aria-label="Lane filter">
          {LANES.map((l) => (
            <button
              key={l.key}
              type="button"
              className={"pb-lane-chip" + (lane === l.key ? " pb-lane-active" : "")}
              onClick={() => setLane(l.key)}
            >
              {l.label} <span className="pb-lane-count">{counts[l.key]}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="pb-list-rows">
        {grouped.length === 0 && (
          <p className="pb-empty muted">No requests match.</p>
        )}
        {grouped.map(([tag, rows]) => (
          <div key={tag} className="pb-group">
            <div className="pb-group-header">
              <span className="pb-group-name">{tag}</span>
              <span className="pb-group-count muted">{rows.length}</span>
            </div>
            {rows.map((r) => (
              <ProbeListRow
                key={r.operation}
                req={r}
                selected={r.operation === selectedOp}
                isShadow={r.endpoint_hashes.some((h) => shadowHashes.has(h))}
                onSelect={() => onSelect(r.operation)}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
