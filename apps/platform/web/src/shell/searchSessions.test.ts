import { describe, it, expect } from "vitest";
import { matchSessions } from "./searchSessions";
import type { SessionSummary } from "../api/types";

const s = (over: Partial<SessionSummary>): SessionSummary => ({
  session_id: "sid", external_id: null, name: null, host: "app.target.com", scope_hosts: [],
  engagement_id: null, archived: false, created_at: null, latest_run: null,
  files: null, endpoints: null, secrets: null, coverage_pct: null, ...over,
});

const sessions = [
  s({ session_id: "a", name: "Acme prod", host: "app.acme.io", scope_hosts: ["acme.io"] }),
  s({ session_id: "b", name: "Beta", host: "beta.example.com", external_id: "ext-42" }),
  s({ session_id: "c", name: null, host: "shop.store.com", scope_hosts: ["store.com", "cdn.store.com"] }),
];

describe("matchSessions (D54 global search)", () => {
  it("returns nothing for an empty query", () => {
    expect(matchSessions(sessions, "")).toEqual([]);
    expect(matchSessions(sessions, "   ")).toEqual([]);
  });

  it("matches by name, host, external id, and scope host (case-insensitive)", () => {
    expect(matchSessions(sessions, "acme").map((m) => m.session_id)).toEqual(["a"]);
    expect(matchSessions(sessions, "beta.example").map((m) => m.session_id)).toEqual(["b"]);
    expect(matchSessions(sessions, "EXT-42").map((m) => m.session_id)).toEqual(["b"]);
    expect(matchSessions(sessions, "cdn.store").map((m) => m.session_id)).toEqual(["c"]);
  });

  it("caps the result count", () => {
    const many = Array.from({ length: 20 }, (_, i) => s({ session_id: `x${i}`, host: "match.dev" }));
    expect(matchSessions(many, "match.dev", 8)).toHaveLength(8);
  });
});
