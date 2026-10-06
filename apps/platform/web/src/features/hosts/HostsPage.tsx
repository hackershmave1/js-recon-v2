import { useMemo, useState } from "react";
import type { HostsResponse, HostRow } from "../../api/types";
import { Icon } from "../../shell/icons";
import { typeLabel } from "../../api/findingLabels";
import "./hosts.css";

// The discovered-host inventory (DEBT D26): EVERY host recon surfaced — from fetched
// assets, resolved-host endpoints, suspected-backend calls, client-navigation page
// routes, tech detection, and declared base-URL rules — with an in/out-of-scope badge
// (the canonical egress classification the server computed) and per-host roll-up counts.
// Filterable by scope and by name, sortable by any count. Honesty (design §5): one column
// per Findings lane, named with that lane's Type-facet label — "API" (confirmed only),
// "Inferred API", "Suspected calls" (generic/unresolved, DEBT D24/D26) and "Page routes"
// (client-nav targets, QA #5) — so no lane is blended or renamed between pages. Each
// endpoint lane's host-less total is surfaced in the summary, so a column plus its
// "no host" count adds up to the same lane's number in the Overview Endpoints split.

type ScopeFilter = "all" | "in" | "out";
type SortKey = "host" | "assets" | "endpoints" | "inferred" | "suspected" | "routes" | "techs";

const SCOPE_LABEL: Record<ScopeFilter, string> = { all: "All", in: "In scope", out: "Out of scope" };

export function HostsPage({ data }: { data: HostsResponse }) {
  const [scope, setScope] = useState<ScopeFilter>("all");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("host");
  const [sortAsc, setSortAsc] = useState(true);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const dir = sortAsc ? 1 : -1;
    return data.hosts
      .filter((h) => {
        if (scope === "in" && !h.in_scope) return false;
        if (scope === "out" && h.in_scope) return false;
        return !needle || h.host.includes(needle);
      })
      .sort((a, b) =>
        sortKey === "host" ? a.host.localeCompare(b.host) * dir : (a[sortKey] - b[sortKey]) * dir,
      );
  }, [data.hosts, scope, query, sortKey, sortAsc]);

  if (data.count === 0) {
    return (
      <div className="card">
        <h2 className="rp-title">Hosts</h2>
        <p className="muted">No hosts discovered for this run yet.</p>
      </div>
    );
  }

  const out = data.count - data.in_scope;
  // Host-less findings per endpoint lane, in the lanes' own labels (see header comment).
  const hostless = [
    [data.endpoints_unattributed, typeLabel("endpoint")],
    [data.inferred_unattributed, typeLabel("endpoint_suspected")],
    [data.suspected_unattributed, typeLabel("endpoint_unresolved")],
  ] as const;
  const hostlessParts = hostless
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}${n === 1 || label === "API" ? "" : "s"}`);
  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc((asc) => !asc);
    else { setSortKey(key); setSortAsc(key === "host"); } // counts default high→low
  };
  const arrow = (key: SortKey) => (sortKey === key ? (sortAsc ? " ▲" : " ▼") : "");
  const numHead = (key: SortKey, label: string, title?: string) => (
    <th className="hosts-num">
      <button type="button" className="hosts-sortbtn" title={title} onClick={() => toggleSort(key)}>
        {label}{arrow(key)}
      </button>
    </th>
  );

  return (
    <div className="card">
      <div className="hosts-head">
        <h2 className="rp-title">Hosts</h2>
        <p className="hosts-sub muted">
          {data.count} discovered · {data.in_scope} in scope · {out} out of scope
          {hostlessParts.length > 0 && (
            <>
              {" · "}
              <span className="hosts-note">no resolved host: {hostlessParts.join(" · ")}</span>
            </>
          )}
        </p>
      </div>

      <div className="hosts-controls">
        <div className="hosts-scope" role="group" aria-label="Filter by scope">
          {(["all", "in", "out"] as ScopeFilter[]).map((s) => (
            <button
              key={s}
              type="button"
              className={"hosts-seg" + (scope === s ? " is-active" : "")}
              aria-pressed={scope === s}
              onClick={() => setScope(s)}
            >
              {SCOPE_LABEL[s]}
            </button>
          ))}
        </div>
        <input
          className="hosts-filter"
          type="search"
          placeholder="Filter by name…"
          aria-label="Filter hosts by name"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {rows.length === 0 ? (
        <p className="muted hosts-empty">No hosts match this filter.</p>
      ) : (
        <table className="hosts-table">
          <thead>
            <tr>
              <th>
                <button type="button" className="hosts-sortbtn" onClick={() => toggleSort("host")}>
                  Host{arrow("host")}
                </button>
              </th>
              <th>Scope</th>
              {numHead("assets", "Assets")}
              {numHead("endpoints", "API", "Confirmed APIs whose host resolved")}
              {numHead(
                "inferred",
                "Inferred API",
                "Inferred APIs (a valid path recovered from a generic or unresolved call) whose host resolved",
              )}
              {numHead(
                "suspected",
                "Suspected calls",
                "Suspected backend calls (URL not statically resolved) whose host resolved — not a confirmed API",
              )}
              {numHead(
                "routes",
                "Page routes",
                "Client-navigation / referenced hosts (page routes) — not a backend the client calls",
              )}
              {numHead("techs", "Tech")}
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <HostTableRow key={h.host} row={h} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// A host's scope is shown by icon + label (never colour alone — accessibility);
// `declared` marks a host known only from an operator base-URL rule (REQ-C2).
function HostTableRow({ row }: { row: HostRow }) {
  return (
    <tr>
      <td className="hosts-host">
        <span className="hosts-host-name">{row.host}</span>
        {row.declared && (
          <span className="hosts-declared" title="Declared via a base-URL rule">declared</span>
        )}
      </td>
      <td>
        <span className={"hosts-scope-badge " + (row.in_scope ? "is-in" : "is-out")}>
          <Icon name={row.in_scope ? "shield" : "alert"} size={13} />
          {row.in_scope ? "in scope" : "out of scope"}
        </span>
      </td>
      <td className="hosts-num">{row.assets}</td>
      <td className="hosts-num">{row.endpoints}</td>
      <td className="hosts-num">{row.inferred}</td>
      <td className="hosts-num">
        {row.suspected > 0 ? (
          <span className="hosts-suspected-val">{row.suspected}</span>
        ) : (
          row.suspected
        )}
      </td>
      <td className="hosts-num">{row.routes}</td>
      <td className="hosts-num">{row.techs}</td>
    </tr>
  );
}
