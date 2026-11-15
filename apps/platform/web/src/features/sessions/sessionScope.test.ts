import { describe, expect, it } from "vitest";
import { scopeSummary, sessionTitle } from "./sessionScope";

describe("scopeSummary", () => {
  it("says so when a session has no scope", () => {
    expect(scopeSummary([]).text).toBe("No scope set");
    expect(scopeSummary(["", ""]).text).toBe("No scope set");
  });

  it("lists up to two hosts and counts the rest, with every host in the tooltip", () => {
    expect(scopeSummary(["acme.io"]).text).toBe("acme.io");
    expect(scopeSummary(["acme.io", "api.acme.io"]).text).toBe("acme.io, api.acme.io");
    const three = scopeSummary(["acme.io", "api.acme.io", "cdn.acme-static.com"]);
    expect(three.text).toBe("acme.io, api.acme.io +1");
    expect(three.title).toBe("acme.io, api.acme.io, cdn.acme-static.com");
  });
});

describe("sessionTitle", () => {
  it("prefers a name, then a real host", () => {
    expect(sessionTitle({ name: "Q4 review", host: "acme.io" })).toBe("Q4 review");
    expect(sessionTitle({ name: null, host: "acme.io" })).toBe("acme.io");
  });

  it("names a session with no host instead of printing a dash", () => {
    expect(sessionTitle({ name: null, host: "—" })).toBe("Empty session");
    expect(sessionTitle({ name: null, host: "" })).toBe("Empty session");
  });
});
