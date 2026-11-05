import type { SourceMatch, SourceSearchResponse, SourcesIndexStatus } from "../../api/types";

// D52: full-text search results, shown in place of the file tree while a query is active. The
// summary says exactly what was searched: a capped list never reads as "all matches", a search the
// time budget (or a newer query) cut short says so, and a run analyzed before search indexing says
// its hits carry no line number (they were searched raw — clicking opens the file).
export function SourceSearchResults({ response, searching, error, indexStatus = null, scopeLabel = null, onJump }: {
  response: SourceSearchResponse | null; searching: boolean; error: string | null;
  indexStatus?: SourcesIndexStatus | null; scopeLabel?: string | null; onJump: (m: SourceMatch) => void;
}) {
  if (error) return <div className="sv-search-note sv-search-error">{error}</div>;
  if (!response || (searching && response.matches.length === 0)) {
    return <div className="sv-search-note">Searching…</div>;
  }
  const shown = response.count;
  const summary = `${shown < response.total ? `Showing ${shown} of ${response.total}` : `${response.total}`} `
    + `${response.total === 1 ? "match" : "matches"} · searched ${response.files_scanned} of ${response.files_total} files`
    + (scopeLabel ? ` ${scopeLabel}` : "")
    + (response.third_party_excluded ? ` · ${response.third_party_excluded} third-party excluded` : "");
  return (
    <div className="sv-search-wrap">
      <div className="sv-search-note">{searching ? "Searching…" : summary}</div>
      {response.partial && (
        <div className="sv-search-note sv-search-warn">Search stopped early — refine the query for complete results.</div>
      )}
      {response.unindexed > 0 && (indexStatus?.indexing ? (
        <div className="sv-search-note">
          Indexing older files in the background ({indexStatus.indexed} of {indexStatus.total} done) —
          line numbers fill in as they finish.
        </div>
      ) : (
        <div className="sv-search-note">
          {response.unindexed} files analyzed before search indexing: their matches show no line number.
        </div>
      ))}
      {shown === 0 ? (
        <div className="sv-search-note">No matches.</div>
      ) : (
        <ul className="sv-search-results">
          {response.matches.map((m, i) => {
            const name = m.path.split("/").filter(Boolean).slice(-1)[0] || m.path;
            const loc = m.line != null ? `${name}:${m.line}` : name;
            return (
              <li key={`${m.path}:${m.line ?? m.col}:${i}`}>
                <button type="button" className="sv-search-hit" onClick={() => onJump(m)}
                  title={m.line != null ? `${m.path}:${m.line}` : m.path}>
                  <span className="sv-search-hit-loc">{loc}</span>
                  <span className="sv-search-hit-snip">{m.snippet}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
