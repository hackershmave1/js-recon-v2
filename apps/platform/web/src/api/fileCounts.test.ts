import { describe, it, expect } from "vitest";
import { collectedLabel, hiddenLabel, recoveredLabel } from "./fileCounts";

// The Overview and the Sources page both phrase the file counts with these, so the same
// number never reads differently on the two screens (walkthrough F14).
describe("fileCounts labels", () => {
  it("phrases collected files and recovered originals identically everywhere", () => {
    expect(collectedLabel(1)).toBe("1 file collected");
    expect(collectedLabel(30)).toBe("30 files collected");
    expect(recoveredLabel(63)).toBe("63 original sources recovered");
    expect(recoveredLabel(1)).toBe("1 original source recovered");
  });

  it("names why files are hidden, and says nothing when none are", () => {
    expect(hiddenLabel(4)).toBe("4 hidden: out of scope");
    expect(hiddenLabel(0)).toBeNull();
    expect(hiddenLabel(undefined)).toBeNull();
  });
});
