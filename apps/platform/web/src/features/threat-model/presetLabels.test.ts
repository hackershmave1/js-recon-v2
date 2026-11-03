import { describe, it, expect } from "vitest";
import { defaultOptionLabel, overrideNote } from "./presetLabels";
import type { RunPresets } from "./threatModelApi";

const MENU: RunPresets = {
  credential_provider: "openrouter",
  default_model: "deepseek/deepseek-v4-flash-0731",
  key_source: "team",
  presets: { cheapest: "anthropic/claude-haiku-4.5:floor", balanced: "anthropic/claude-sonnet-4.6", strongest: "anthropic/claude-opus-5.5" },
};
const CATALOG = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed" as const, runs: 0 },
  models: [{ id: "deepseek/deepseek-v4-flash-0731", name: "DeepSeek", context_length: 1, max_completion_tokens: null, prompt_price: "0.0000003", completion_price: "0.0000012" }],
};

describe("defaultOptionLabel", () => {
  it("names the model that will run, whose key pays, and its cost", () => {
    expect(defaultOptionLabel(MENU, CATALOG)).toMatch(/^Default · deepseek\/deepseek-v4-flash-0731 \(team key\) · /);
  });
  it("falls back to the old label when the server doesn't say", () => {
    expect(defaultOptionLabel({ ...MENU, default_model: undefined }, null)).toBe("Default model");
    expect(defaultOptionLabel(null, null)).toBe("Default model");
  });
});

describe("overrideNote", () => {
  it("warns that a preset replaces the Settings choice", () => {
    expect(overrideNote(MENU, "balanced")).toBe(
      "Uses anthropic/claude-sonnet-4.6 instead of the default (deepseek/deepseek-v4-flash-0731)",
    );
  });
  it("says nothing for the default, or when the preset is the same model", () => {
    expect(overrideNote(MENU, "")).toBeNull();
    expect(overrideNote({ ...MENU, default_model: "anthropic/claude-sonnet-4.6" }, "balanced")).toBeNull();
  });
  it("names the key in the label, not as the model's owner", () => {
    expect(defaultOptionLabel({ ...MENU, key_source: "env" }, null)).toBe("Default · deepseek/deepseek-v4-flash-0731 (server key)");
  });
});
