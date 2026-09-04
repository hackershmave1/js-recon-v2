import { describe, it, expect } from "vitest";
import { shouldNotifyRunFinished } from "./runNotify";

const ctx = { permission: "granted", hidden: true };

describe("shouldNotifyRunFinished (D54)", () => {
  it("fires on the transition into a terminal state when hidden + granted", () => {
    expect(shouldNotifyRunFinished("running", "done", ctx)).toBe(true);
    expect(shouldNotifyRunFinished("running", "partial", ctx)).toBe(true);
    expect(shouldNotifyRunFinished("running", "failed", ctx)).toBe(true);
  });

  it("does not fire when already terminal (no double-notify on a terminal re-render)", () => {
    expect(shouldNotifyRunFinished("done", "done", ctx)).toBe(false);
    expect(shouldNotifyRunFinished("done", "partial", ctx)).toBe(false);
  });

  it("does not fire on a non-terminal transition", () => {
    expect(shouldNotifyRunFinished("queued", "running", ctx)).toBe(false);
  });

  it("requires granted permission", () => {
    expect(shouldNotifyRunFinished("running", "done", { permission: "default", hidden: true })).toBe(false);
    expect(shouldNotifyRunFinished("running", "done", { permission: "denied", hidden: true })).toBe(false);
  });

  it("does not fire while the tab is visible (the operator is watching)", () => {
    expect(shouldNotifyRunFinished("running", "done", { permission: "granted", hidden: false })).toBe(false);
  });
});
