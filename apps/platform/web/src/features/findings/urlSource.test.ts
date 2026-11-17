import { describe, it, expect } from "vitest";
import type { Occurrence } from "../../api/types";
import { urlSources } from "./urlSource";

const occ = (url_source?: string | null) => ({ url_source }) as Occurrence;

describe("urlSources", () => {
  it("returns distinct page sources in first-seen order", () => {
    expect(urlSources([occ("{attr:data-url}"), occ(null), occ("{form:action}"), occ("{attr:data-url}")]))
      .toEqual(["{attr:data-url}", "{form:action}"]);
  });

  it("is empty when no occurrence (or an old API row) names a source", () => {
    expect(urlSources([occ(null), occ(undefined)])).toEqual([]);
  });
});
