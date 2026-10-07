import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPage } from "./SettingsPage";
import * as api from "./settingsApi";
import * as catalogApi from "../../api/llmCatalog";
import type { TeamLlmSettings } from "./settingsApi";

const BASE: TeamLlmSettings = {
  config: null,
  can_edit: true,
  default_models: { anthropic: "claude-default", openrouter: "or-default", gemini: "gem-default" },
  providers: ["anthropic", "gemini", "openrouter"],
  builtin_preset_models: {}, presets: null,
};
const SAVED: TeamLlmSettings = {
  ...BASE,
  config: {
    provider: "openrouter", model: "or-model", has_key: true,
    configured_at: "2026-10-06T00:00:00Z", configured_by: "admin@acme.io", tested_at: null, preset_models: {},
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(catalogApi, "getModelCatalog").mockResolvedValue({ available: false, stale: false, fetched_at: null, models: [], estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed", runs: 0 } });
});

describe("SettingsPage", () => {
  it("admin saves the typed key, and the key field is empty again afterwards", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValueOnce(BASE).mockResolvedValueOnce(SAVED);
    const save = vi.spyOn(api, "saveTeamLlmSettings").mockResolvedValue(SAVED.config!);
    render(<SettingsPage tenantId="t1" />);
    const key = await screen.findByLabelText("API key");
    expect(screen.getByLabelText("Model")).toHaveValue("claude-default");
    await userEvent.type(key, "sk-typed");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).toHaveBeenCalledWith("t1", { provider: "anthropic", model: "claude-default", api_key: "sk-typed" });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });

  it("switching provider swaps in that provider's default model", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue(BASE);
    render(<SettingsPage tenantId="t1" />);
    await userEvent.selectOptions(await screen.findByLabelText("Provider"), "openrouter");
    expect(screen.getByLabelText("Model")).toHaveValue("or-default");
  });

  it("a saved key is never rendered, only flagged", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue(SAVED);
    render(<SettingsPage tenantId="t1" />);
    const key = await screen.findByLabelText("API key");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "A key is saved. Leave blank to keep it.");
    expect(screen.getByText(/admin@acme\.io/)).toBeInTheDocument();
  });

  it("switching provider when a key is saved requires a new key", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue(SAVED);
    render(<SettingsPage tenantId="t1" />);
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeEnabled(); // same provider: blank keeps the saved key
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "anthropic");
    expect(save).toBeDisabled();
    const key = screen.getByLabelText("API key");
    expect(key).toHaveAttribute("placeholder", "Enter a key for this provider");
    await userEvent.type(key, "sk-anthropic");
    expect(save).toBeEnabled();
  });

  it("analyst sees a read-only view with no key field", async () => {
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue({ ...SAVED, can_edit: false });
    render(<SettingsPage tenantId="t1" />);
    expect(await screen.findByText("Only admins can change the team key.")).toBeInTheDocument();
    expect(screen.getByText("or-model")).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("a preset save sends the saved provider/model with a blank key and the preset map", async () => {
    const withPresets: TeamLlmSettings = {
      ...SAVED,
      presets: {
        cheapest: { model: "vendor/small", source: "builtin", available: null },
        balanced: { model: "vendor/mid", source: "builtin", available: null },
        strongest: { model: "vendor/big", source: "builtin", available: null },
      },
    };
    vi.spyOn(api, "getTeamLlmSettings").mockResolvedValue(withPresets);
    const save = vi.spyOn(api, "saveTeamLlmSettings").mockResolvedValue(SAVED.config!);
    render(<SettingsPage tenantId="t1" />);
    await screen.findByText("Presets");
    // Unsaved edits in the main form must not leak into a presets-only PUT.
    await userEvent.type(await screen.findByLabelText("Model"), "-unsaved");
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const input = screen.getByLabelText(/Cheapest model/);
    await userEvent.clear(input);
    await userEvent.type(input, "vendor/tiny");
    await userEvent.click(screen.getByRole("button", { name: "Save preset" }));
    expect(save).toHaveBeenCalledWith("t1", {
      provider: "openrouter", model: "or-model", api_key: "", preset_models: { cheapest: "vendor/tiny" },
    });
  });
});
