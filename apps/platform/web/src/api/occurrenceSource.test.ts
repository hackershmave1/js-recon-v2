import { describe, it, expect } from "vitest";
import type { Finding, Occurrence } from "./types";
import { occLocation, occSource, primaryOccurrence } from "./occurrenceSource";

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: null, line: null, col: null,
  offset_start: null, offset_end: null, evidence: null, engine: null,
  confidence: null, verified: null, asset_url: null, ...over,
});
const finding = (occurrences: Occurrence[]): Finding => ({
  finding_hash: "h", type: "secret", value: "aws:abc", path: "input.js", severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences,
});

const BUNDLE = "http://t.test/assets/index-BLBrOdfO.js";
// The QA shape: one secret sighted in the minified bundle (placeholder path) AND in its
// source-map-recovered original.
const bundleSighting = occ({ source_path: "input.js", line: 1, asset_url: BUNDLE });
const recoveredSighting = occ({ source_path: "src/secrets.js", line: 3, asset_url: BUNDLE });

describe("occurrenceSource", () => {
  it("never labels a bundle sighting with the input.js placeholder — the real bundle URL instead", () => {
    expect(occLocation(bundleSighting)).toBe(`${BUNDLE}:1`);
  });

  it("names a recovered original by its real path, with the owning bundle as secondary", () => {
    expect(occSource(recoveredSighting)).toEqual({ primary: "src/secrets.js", bundle: BUNDLE });
    expect(occLocation(recoveredSighting)).toBe("src/secrets.js:3");
  });

  it("picks the recovered original over the bundle sighting as the primary occurrence", () => {
    expect(primaryOccurrence(finding([bundleSighting, recoveredSighting]))).toBe(recoveredSighting);
  });

  it("falls back to the first occurrence when none was recovered", () => {
    expect(primaryOccurrence(finding([bundleSighting]))).toBe(bundleSighting);
    expect(primaryOccurrence(finding([]))).toBeUndefined();
  });

  it("keeps input.js only as the last resort for a legacy upload with no bundle URL", () => {
    expect(occLocation(occ({ source_path: "input.js", line: 2 }))).toBe("input.js:2");
  });

  it("returns no location for a host-only occurrence instead of inventing one", () => {
    expect(occLocation(occ({ host: "api.acme.com" }))).toBeNull();
  });
});
