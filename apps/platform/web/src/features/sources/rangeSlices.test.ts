import { describe, it, expect } from "vitest";
import { rangesByLine, renderWindowStart, splitByRanges } from "./rangeSlices";

describe("rangeSlices", () => {
  it("indexes each range on every line it covers, with that line's column bounds", () => {
    const byLine = rangesByLine([{ line: 2, col: 4, end_line: 3, end_col: 2 }], null);
    expect(byLine.get(2)).toEqual([{ start: 4, end: Infinity, focus: false }]);
    expect(byLine.get(3)).toEqual([{ start: 0, end: 2, focus: false }]);
  });

  it("flags the jumped-to range as the focus", () => {
    const loc = { line: 1, col: 0, end_line: 1, end_col: 3 };
    expect(rangesByLine([loc], loc).get(1)).toEqual([{ start: 0, end: 3, focus: true }]);
  });

  it("splits highlighted spans exactly at the range columns", () => {
    const spans = [{ text: "fetch", className: "token function" }, { text: '("/api/a")', className: "" }];
    const out = splitByRanges(spans, [{ start: 6, end: 14, focus: true }]);
    expect(out.map((p) => [p.text, p.hit])).toEqual([
      ["fetch", null], ["(", null], ['"/api/a"', "focus"], [")", null],
    ]);
  });
});

describe("rendering a window of a huge row", () => {
  it("centres the window on the focused range and clamps it to the row", () => {
    const hits = [{ start: 10, end: 12, focus: false }, { start: 3_400_010, end: 3_400_030, focus: true }];
    expect(renderWindowStart(9_000_000, hits, 100_000)).toBe(3_350_010);
    expect(renderWindowStart(3_400_100, hits, 100_000)).toBe(3_300_100); // clamped to the end
    expect(renderWindowStart(50_000, hits, 100_000)).toBe(0);            // fits whole
  });
});
