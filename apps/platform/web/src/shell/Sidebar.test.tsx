import { describe, expect, it } from "vitest";
import { runCardLines } from "./Sidebar";

// The card sits under the heading ENGAGEMENT on every run page. It used to show the session's
// host and the run id there, and the engagement the capture was started in appeared nowhere.
describe("runCardLines", () => {
  const run = { domain: "app.acme.io", runId: "69ae90b0-f07d-4500-a3d8-8a015b976732" };

  it("names the run's engagement, with the target and short run id beneath", () => {
    expect(runCardLines({ ...run, engagement: { engagement_id: "e1", name: "Acme Q4 review" } }))
      .toEqual({ name: "Acme Q4 review", detail: "app.acme.io · 69ae90b0" });
  });

  it("says a standalone session has no engagement", () => {
    expect(runCardLines({ ...run, engagement: null }).name).toBe("No engagement");
  });

  it("claims nothing while the engagement is unknown", () => {
    expect(runCardLines({ ...run, engagement: undefined }).name).toBe("—");
  });

  it("never puts the host on the engagement line", () => {
    for (const engagement of [undefined, null, { engagement_id: "e1", name: "Acme Q4 review" }]) {
      expect(runCardLines({ ...run, engagement }).name).not.toContain("acme.io");
    }
  });

  it("falls back for an upload run with no target", () => {
    expect(runCardLines({ engagement: null, domain: null }).detail).toBe("Current run");
    expect(runCardLines({ engagement: null, domain: null, runId: "abcdef1234" }).detail).toBe("abcdef12");
  });
});
