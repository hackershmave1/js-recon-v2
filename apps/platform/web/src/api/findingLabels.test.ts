import { describe, it, expect } from "vitest";
import { typeLabel } from "./findingLabels";

describe("typeLabel", () => {
  it("relabels the confirmed endpoint lane as API and the nav lane as page route", () => {
    expect(typeLabel("endpoint")).toBe("API");
    expect(typeLabel("page_route")).toBe("page route");
  });

  it("keeps the two unconfirmed-lane confidence tiers", () => {
    expect(typeLabel("endpoint_unresolved")).toBe("unconfirmed");
    expect(typeLabel("endpoint_generic")).toBe("generic call");
  });

  it("falls back to the raw wire token for types with no human label", () => {
    expect(typeLabel("secret")).toBe("secret");
    expect(typeLabel("param")).toBe("param");
  });
});
