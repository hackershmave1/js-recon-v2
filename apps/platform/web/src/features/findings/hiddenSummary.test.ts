import { describe, it, expect } from "vitest";
import { hiddenSummary, hiddenTotal } from "./hiddenSummary";

describe("hiddenSummary", () => {
  it("names each reason with its count", () => {
    const hidden = { off_scope: 7, junk_or_dynamic: 10, noise: 5 };
    expect(hiddenTotal(hidden)).toBe(22);
    expect(hiddenSummary(hidden)).toBe("junk 10 · analytics 5 · off-scope 7");
  });
  it("is empty when nothing is hidden or the server predates the field", () => {
    expect(hiddenTotal(undefined)).toBe(0);
    expect(hiddenSummary({})).toBe("");
  });
});
