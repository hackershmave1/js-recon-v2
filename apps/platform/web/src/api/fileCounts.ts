import type { SourcesResponse } from "./types";

// The Overview and the Sources page both phrase the run's file counts with these helpers, so one
// figure never reads two ways (walkthrough F14). The numbers come from the backend's single
// definitions (queries.file_counts); nothing here computes or invents one.

export const collectedLabel = (n: number): string => `${n} file${n === 1 ? "" : "s"} collected`;

export const recoveredLabel = (n: number): string => `${n} original source${n === 1 ? "" : "s"} recovered`;

// Files fetched but hidden because their host was removed from the session scope (the Hosts page
// brings them back). null when nothing is hidden, so a clean run says nothing extra.
export const hiddenLabel = (n: number | undefined): string | null =>
  n ? `${n} hidden: out of scope` : null;

// The Sources page's counts line: the same pair the Overview shows, from the same backend figures.
export function sourcesCountsLine(counts: SourcesResponse["counts"]): string | null {
  if (!counts) return null;
  return [collectedLabel(counts.collected), recoveredLabel(counts.sources_recovered), hiddenLabel(counts.hidden_out_of_scope)]
    .filter(Boolean).join(" · ");
}
