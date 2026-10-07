import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PresetRows } from "./PresetRows";
import type { TeamLlmSettings } from "./settingsApi";
import type { ModelCatalog } from "../../api/llmCatalog";

const CATALOG: ModelCatalog = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed", runs: 0 },
  models: [
    { id: "anthropic/claude-haiku-4.5", name: "Claude Haiku 4.5", context_length: 200000, max_completion_tokens: 64000, prompt_price: "0.000001", completion_price: "0.000005" },
    { id: "anthropic/claude-opus-5.5", name: "Claude Opus 5.5", context_length: 1000000, max_completion_tokens: 128000, prompt_price: "0.000004", completion_price: "0.00002" },
  ],
};

function settings(over: Partial<TeamLlmSettings> = {}): TeamLlmSettings {
  return {
    can_edit: true,
    default_models: {},
    providers: ["anthropic", "gemini", "openrouter"],
    builtin_preset_models: {},
    config: {
      provider: "openrouter", model: "anthropic/claude-sonnet-4.6", has_key: true,
      configured_at: null, configured_by: null, tested_at: null, preset_models: { strongest: "vendor/big" },
    },
    presets: {
      cheapest: { model: "anthropic/claude-haiku-4.5", source: "builtin", available: true },
      balanced: { model: "anthropic/claude-sonnet-4.6", source: "builtin", available: null },
      strongest: { model: "vendor/big", source: "team", available: false },
    },
    ...over,
  };
}

describe("PresetRows", () => {
  it("shows each preset's model, source, cost and catalog warnings", () => {
    render(<PresetRows settings={settings()} catalog={CATALOG} busy={false} onSave={vi.fn()} />);
    expect(screen.getByText("anthropic/claude-haiku-4.5")).toBeInTheDocument();
    expect(screen.getByText("≈ $0.04")).toBeInTheDocument();
    expect(screen.getAllByText("default")).toHaveLength(2);
    expect(screen.getByText("team")).toBeInTheDocument();
    expect(screen.getByText("not in catalog")).toBeInTheDocument();
  });
  it("editing a preset through the picker saves the full override map", async () => {
    const onSave = vi.fn();
    render(<PresetRows settings={settings()} catalog={CATALOG} busy={false} onSave={onSave} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await userEvent.click(screen.getByRole("button", { name: /Claude Opus 5.5/ }));
    expect(onSave).toHaveBeenCalledWith({ strongest: "vendor/big", cheapest: "anthropic/claude-opus-5.5" });
  });
  it("reset removes the override, clearing the map when it was the last one", async () => {
    const onSave = vi.fn();
    render(<PresetRows settings={settings()} catalog={CATALOG} busy={false} onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onSave).toHaveBeenCalledWith(null);
  });
  it("analysts see presets read-only", () => {
    render(<PresetRows settings={settings({ can_edit: false })} catalog={CATALOG} busy={false} onSave={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
  it("non-OpenRouter teams edit a preset as text", async () => {
    const onSave = vi.fn();
    const s = settings();
    s.config = { ...s.config!, provider: "anthropic", preset_models: {} };
    render(<PresetRows settings={s} catalog={null} busy={false} onSave={onSave} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    const input = screen.getByLabelText(/Balanced model/);
    await userEvent.clear(input);
    await userEvent.type(input, "claude-y");
    await userEvent.click(screen.getByRole("button", { name: "Save preset" }));
    expect(onSave).toHaveBeenCalledWith({ balanced: "claude-y" });
  });
});
