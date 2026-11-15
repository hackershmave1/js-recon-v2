import { describe, expect, it } from "vitest";
import { techStackHidden } from "./techVisibility";
import type { TechnologiesResponse } from "../api/types";

const none: TechnologiesResponse = { run_id: "r", count: 0, hosts: {}, detection_ran: false };
const found: TechnologiesResponse = { run_id: "r", count: 1, hosts: { "acme.io": [] }, detection_ran: true };

describe("techStackHidden", () => {
  it("hides the tab and tile for a finished run where detection never ran", () => {
    expect(techStackHidden(none, "done")).toBe(true);
    expect(techStackHidden(none, "partial")).toBe(true);
    expect(techStackHidden(none, "capturing")).toBe(true);
  });

  it("keeps them while the run is still active, so 'awaiting analysis' can show", () => {
    expect(techStackHidden(none, "analyzing")).toBe(false);
    expect(techStackHidden(none, "queued")).toBe(false);
  });

  it("keeps them when technologies were detected, or when nothing is known yet", () => {
    expect(techStackHidden(found, "done")).toBe(false);
    expect(techStackHidden(null, "done")).toBe(false);
    expect(techStackHidden(undefined, "done")).toBe(false);
    expect(techStackHidden({ ...none, detection_ran: undefined }, "done")).toBe(false);
  });
});
