import { describe, it, expect, beforeEach, vi } from "vitest";
import * as api from "../api/apiClient";
import {
  captureHandoffFragment,
  hasHandoffCode,
  resetHandoffForTests,
  startHandoff,
} from "./handoffFragment";

const CODE = "A".repeat(43);
const LOGIN = { token: "t.s", user: "u", role: "admin", tenant: { id: "t", name: "T" } };

function visit(url: string) {
  window.history.replaceState(null, "", url);
}

beforeEach(() => {
  vi.restoreAllMocks();
  resetHandoffForTests();
  visit("/");
});

describe("handoff fragment", () => {
  it("lifts the code out of the address bar and keeps the path and query", () => {
    visit(`/runs/7?capture=ext-1#handoff=${CODE}`);
    captureHandoffFragment();
    expect(window.location.hash).toBe("");
    expect(window.location.pathname + window.location.search).toBe("/runs/7?capture=ext-1");
    expect(window.location.href).not.toContain(CODE);
    expect(hasHandoffCode()).toBe(true);
  });

  it("does not add a history entry (it rewrites the current one)", () => {
    const before = window.history.length;
    visit(`/#handoff=${CODE}`);
    const afterVisit = window.history.length;
    captureHandoffFragment();
    expect(window.history.length).toBe(afterVisit);
    expect(afterVisit).toBe(before);
  });

  it("strips a malformed handoff fragment too, and captures nothing", () => {
    visit("/#handoff=short");
    captureHandoffFragment();
    expect(window.location.hash).toBe("");
    expect(hasHandoffCode()).toBe(false);
  });

  it("ignores an unrelated fragment", () => {
    visit("/#section-2");
    captureHandoffFragment();
    expect(window.location.hash).toBe("#section-2");
    expect(hasHandoffCode()).toBe(false);
  });

  it("spends the code exactly once even if started twice (StrictMode)", async () => {
    const spy = vi.spyOn(api, "exchangeHandoffCode").mockResolvedValue(LOGIN);
    visit(`/#handoff=${CODE}`);
    captureHandoffFragment();
    const [a, b] = await Promise.all([startHandoff(), startHandoff()]);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(CODE);
    expect(a).toEqual(LOGIN);
    expect(b).toEqual(LOGIN);
  });

  it("resolves to null on any failure, with no detail", async () => {
    vi.spyOn(api, "exchangeHandoffCode").mockRejectedValue(new api.ApiError(401, "secret detail"));
    visit(`/#handoff=${CODE}`);
    captureHandoffFragment();
    await expect(startHandoff()).resolves.toBeNull();
  });

  it("does nothing when there is no code", async () => {
    const spy = vi.spyOn(api, "exchangeHandoffCode");
    await expect(startHandoff()).resolves.toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});
