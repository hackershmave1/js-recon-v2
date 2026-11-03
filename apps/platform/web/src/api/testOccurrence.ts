import type { Occurrence } from "./types";

// Test fixtures: an occurrence whose `line` is where analyze recorded it gets the matching exact
// `loc` (the first `width` columns of that line) unless the fixture sets `loc` itself — so a
// fixture written as "the finding is on line N" keeps that meaning under the exact-location
// contract.
export function withExactLoc(o: Occurrence, width = 1): Occurrence {
  if (o.loc !== undefined) return o;
  return { ...o, loc: o.line != null ? { line: o.line, col: 0, end_line: o.line, end_col: width } : null };
}
