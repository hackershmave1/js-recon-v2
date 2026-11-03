import type { ViewLoc } from "../../api/types";
import type { HighlightedSpan } from "./highlight";

// One row's slice of a finding range: [start, end) in the row's UTF-16 columns (JS string
// indices — the unit the backend stores). `end` is Infinity when the range runs past the row.
export interface ColRange { start: number; end: number; focus: boolean; }
export interface HitSpan extends HighlightedSpan { hit: null | "hit" | "focus"; }

// A range spanning more rows than this is a whole call/document; only its first rows are
// painted so one huge span can't build a giant index.
const MAX_RANGE_ROWS = 200;

// line -> the slices of every finding range on that line, built once per file.
export function rangesByLine(ranges: ViewLoc[], focus: ViewLoc | null): Map<number, ColRange[]> {
  const byLine = new Map<number, ColRange[]>();
  const add = (loc: ViewLoc, isFocus: boolean) => {
    const last = Math.min(loc.end_line, loc.line + MAX_RANGE_ROWS);
    for (let line = loc.line; line <= last; line++) {
      const slice = {
        start: line === loc.line ? loc.col : 0,
        end: line === loc.end_line ? loc.end_col : Infinity,
        focus: isFocus,
      };
      const row = byLine.get(line) ?? [];
      row.push(slice);
      byLine.set(line, row);
    }
  };
  for (const loc of ranges) if (!sameLoc(loc, focus)) add(loc, false);
  if (focus) add(focus, true);
  return byLine;
}

const sameLoc = (a: ViewLoc, b: ViewLoc | null) =>
  b != null && a.line === b.line && a.col === b.col && a.end_line === b.end_line && a.end_col === b.end_col;

// Cut the row's (syntax-highlighted) spans at every range boundary so each piece is either
// fully inside a range (hit / focus) or outside it. The focus wins where ranges overlap.
export function splitByRanges(spans: HighlightedSpan[], ranges: ColRange[]): HitSpan[] {
  const out: HitSpan[] = [];
  let col = 0;
  for (const span of spans) {
    const spanEnd = col + span.text.length;
    const cuts = new Set<number>([col, spanEnd]);
    for (const r of ranges) {
      if (r.start > col && r.start < spanEnd) cuts.add(r.start);
      if (r.end > col && r.end < spanEnd) cuts.add(r.end);
    }
    const points = [...cuts].sort((a, b) => a - b);
    for (let i = 0; i < points.length - 1; i++) {
      const [from, to] = [points[i], points[i + 1]];
      const covering = ranges.filter((r) => r.start <= from && to <= r.end);
      const hit = covering.some((r) => r.focus) ? "focus" : covering.length ? "hit" : null;
      out.push({ text: span.text.slice(from - col, to - col), className: span.className, hit });
    }
    col = spanEnd;
  }
  return out;
}
