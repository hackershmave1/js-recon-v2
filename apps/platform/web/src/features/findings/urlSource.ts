import type { Occurrence } from "../../api/types";

// #235: an unresolved URL read from the page (`{attr:data-url}`, `{stimulus:url}`). The finding's
// value stays the old skeleton (identity unchanged), so this label is how the researcher learns
// which attribute to look up in the HTML.
export const URL_SOURCE_LABEL = "URL from the page";

// The distinct page sources across a finding's occurrences, in first-seen order. Occurrences
// with the same skeleton can read different attributes, so a row shows every one it merged.
export function urlSources(occurrences: Occurrence[]): string[] {
  return [...new Set(occurrences.map((o) => o.url_source).filter((s): s is string => !!s))];
}

// A row has one line: a merged finding with many sightings shows the first few sources and a
// count, never an unbounded list (the detail lists every occurrence's own source).
const ROW_SOURCE_LIMIT = 3;
export function rowUrlSourceText(sources: string[]): string {
  const shown = sources.slice(0, ROW_SOURCE_LIMIT).join(", ");
  const more = sources.length - ROW_SOURCE_LIMIT;
  return more > 0 ? `${shown} +${more} more` : shown;
}
