import { describe, it, expect } from "vitest";
import { describeRun } from "./describeRun";
import type { LastCall } from "../../api/types";

const call = (over: Partial<Extract<LastCall, { unavailable: false }>> = {}): LastCall => ({
  unavailable: false, provider: "openrouter", model: "anthropic/claude-sonnet-4.6", key_source: "team",
  status: "ok", prompt_tokens: 18203, completion_tokens: 3941, cost_usd: "0.0729", latency_ms: 41200,
  finish_reason: "stop", ...over,
});

describe("describeRun", () => {
  it("states model, whose key paid, tokens in → out, cost and duration", () => {
    expect(describeRun(call())).toBe("anthropic/claude-sonnet-4.6 · team key · 18,203 → 3,941 tokens · $0.07 · 41 s");
  });

  it("says cost unknown rather than guessing", () => {
    expect(describeRun(call({ cost_usd: null, key_source: "env" }))).toContain("server key · 18,203 → 3,941 tokens · cost unknown");
  });

  it("formats long runs and tiny costs", () => {
    expect(describeRun(call({ latency_ms: 123000, cost_usd: "0.003" }))).toMatch(/<\$0\.01 · 2 min 3 s$/);
  });

  it("omits tokens when the provider reported none", () => {
    expect(describeRun(call({ prompt_tokens: null, completion_tokens: null, key_source: "session" })))
      .toBe("anthropic/claude-sonnet-4.6 · session key · $0.07 · 41 s");
  });

  it("an unsaved record reads as unavailable, not as zeros", () => {
    expect(describeRun({ unavailable: true })).toBe("Run details unavailable");
  });
});
