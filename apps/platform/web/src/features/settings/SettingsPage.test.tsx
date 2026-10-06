import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPage } from "./SettingsPage";
import * as api from "./settingsApi";
import type { TeamLlmSettings } from "./settingsApi";

const BASE: TeamLlmSettings = {
  config: null,
  can_edit: true,
  default_models: { anthropic: "claude-default", openrouter: "or-default", gemini: "gem-default" },
  providers: ["anthropic", "gemini", "openrouter"],
};
const SAVED: TeamLlmSettings = {
  ...BASE,
  config: {
    provider: "openrouter", model: "or-model", has_key: true,
    configured_at: "2026-10-06T00:00:00Z", configured_by: "admin@acme.io", tested_at: null,
  },
};

beforeEach(() => { vi.restoreAllMocks(); });

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
});
