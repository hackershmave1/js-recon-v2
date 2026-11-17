import { describe, it, expect } from "vitest";
import type { Occurrence } from "../../api/types";
import { rowUrlSourceText, urlSources } from "./urlSource";

const occ = (url_source?: string | null) => ({ url_source }) as Occurrence;

describe("urlSources", () => {
  it("returns distinct page sources in first-seen order", () => {
    expect(urlSources([occ("{attr:data-url}"), occ(null), occ("{form:action}"), occ("{attr:data-url}")]))
      .toEqual(["{attr:data-url}", "{form:action}"]);
  });

  it("caps the row text at three sources plus a count", () => {
    expect(rowUrlSourceText(["{a:1}", "{a:2}"])).toBe("{a:1}, {a:2}");
    expect(rowUrlSourceText(["{a:1}", "{a:2}", "{a:3}", "{a:4}", "{a:5}"])).toBe("{a:1}, {a:2}, {a:3} +2 more");
  });

  it("is empty when no occurrence (or an old API row) names a source", () => {
    expect(urlSources([occ(null), occ(undefined)])).toEqual([]);
  });
});
