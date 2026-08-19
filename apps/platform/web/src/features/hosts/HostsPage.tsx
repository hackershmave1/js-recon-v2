import { useMemo, useState } from "react";
import type { HostsResponse, HostRow } from "../../api/types";
import { Icon } from "../../shell/icons";
import "./hosts.css";

// The discovered-host inventory (DEBT D26): EVERY host recon surfaced — from fetched
// assets, resolved-host endpoints, suspected-backend calls, client-navigation page
// routes, tech detection, and declared base-URL rules — with an in/out-of-scope badge
// (the canonical egress classification the server computed) and per-host roll-up counts.
// Filterable by scope and by name, sortable by any count. Honesty (design §5):
// "Endpoints" counts only CONFIRMED endpoints whose host resolved; "Suspected" (generic
// /unresolved lanes, DEBT D24/D26) and "Routes" (page_route client-nav targets, QA #5)
// are SEPARATE columns so backend and non-backend hosts never blur — and each endpoint
// lane's host-less total is surfaced in the summary rather than hidden.

type ScopeFilter = "all" | "in" | "out";
type SortKey = "host" | "assets" | "endpoints" | "suspected" | "routes" | "techs";

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
          {data.endpoints_unattributed > 0 && (
            <>
              {" · "}
              <span className="hosts-note">
                {data.endpoints_unattributed} endpoint{data.endpoints_unattributed === 1 ? "" : "s"}{" "}
                with no resolved host
              </span>
            </>
          )}
          {data.suspected_unattributed > 0 && (
            <>
              {" · "}
              <span className="hosts-note">
                {data.suspected_unattributed} suspected with no host
              </span>
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
              {numHead("endpoints", "Endpoints", "Confirmed endpoints whose host resolved")}
              {numHead(
                "suspected",
                "Suspected",
                "Suspected-backend calls (generic / unresolved) whose host resolved — not a confirmed endpoint",
              )}
              {numHead(
                "routes",
                "Routes",
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
