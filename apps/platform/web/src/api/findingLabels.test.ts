import { describe, it, expect } from "vitest";
import { typeLabel } from "./findingLabels";

describe("typeLabel", () => {
  it("relabels the confirmed endpoint lane as API, the promoted lane as inferred API, and the nav lane as page route", () => {
    expect(typeLabel("endpoint")).toBe("API");
    expect(typeLabel("endpoint_suspected")).toBe("inferred API");
    expect(typeLabel("page_route")).toBe("page route");
  });

  it("keeps the two unconfirmed-lane confidence tiers", () => {
    expect(typeLabel("endpoint_unresolved")).toBe("suspected call");
    expect(typeLabel("endpoint_generic")).toBe("generic call");
  });

  it("gives every lane a distinct label so no two read as synonyms in the Type facet", () => {
    const lanes = ["endpoint", "endpoint_suspected", "endpoint_unresolved", "endpoint_generic",
      "page_route", "secret", "secret_suspected"];
    const labels = lanes.map(typeLabel);
    expect(new Set(labels).size).toBe(lanes.length);
    // A bare "endpoint" or "suspected" is ambiguous across lanes — never used on its own.
    expect(labels).not.toContain("endpoint");
    expect(labels).not.toContain("suspected");
    expect(typeLabel("secret_suspected")).toBe("suspected secret");
  });

  it("labels the cleartext internal-IP info-disclosure lane", () => {
    expect(typeLabel("internal_ip")).toBe("internal IP");
  });

  it("labels the data-sink lanes (D52)", () => {
    expect(typeLabel("postmessage_sink")).toBe("postMessage");
    expect(typeLabel("storage_sink")).toBe("storage write");
  });

  it("falls back to the raw wire token for types with no human label", () => {
    expect(typeLabel("secret")).toBe("secret");
    expect(typeLabel("param")).toBe("param");
  });
});
