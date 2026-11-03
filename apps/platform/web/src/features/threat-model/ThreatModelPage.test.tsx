import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { ThreatModelPage } from "./ThreatModelPage";
import * as api from "../../api/apiClient";
import type { ThreatModelResponse } from "../../api/types";
import userEvent from "@testing-library/user-event";
import * as tmApi from "./threatModelApi";
import * as catalogApi from "../../api/llmCatalog";

const CATALOG = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed" as const, runs: 0 },
  models: [{ id: "anthropic/claude-haiku-4.5", name: "Haiku", context_length: 200000, max_completion_tokens: 64000, prompt_price: "0.000001", completion_price: "0.000005" }],
};
const OR_PRESETS = {
  credential_provider: "openrouter",
  presets: { cheapest: "anthropic/claude-haiku-4.5:floor", balanced: "anthropic/claude-sonnet-4.6", strongest: "anthropic/claude-opus-5.5" },
};

vi.mock("../../tenant/TenantContext", () => ({ useTenant: () => ({ tenantId: "t1" }) }));

const failed = (error: string): ThreatModelResponse => ({
  status: "failed", provider: null, model: null, prompt_tokens: null, completion_tokens: null,
  analysis_summary: null, error, generated_at: null, updated_at: null,
});

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(tmApi, "getRunPresets").mockResolvedValue({ credential_provider: null, presets: null });
  vi.spyOn(catalogApi, "getModelCatalog").mockResolvedValue(CATALOG);
});

describe("ThreatModelPage", () => {
  it("links the no-key failure to Settings", async () => {
    vi.spyOn(api, "getThreatModel").mockResolvedValue(failed("no LLM API key: save one for this session, ..."));
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    const link = await screen.findByRole("link", { name: "Set a team key in Settings →" });
    expect(link).toHaveAttribute("href", "/settings");
  });

  it("no Settings link for other failures", async () => {
    vi.spyOn(api, "getThreatModel").mockResolvedValue(failed("provider timeout"));
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByText(/provider timeout/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Settings/ })).toBeNull();
  });

  it("offers presets with costs for an OpenRouter key and sends the chosen one", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    vi.spyOn(tmApi, "getRunPresets").mockResolvedValue(OR_PRESETS);
    const trigger = vi.spyOn(api, "triggerThreatModel").mockResolvedValue({ ...failed("x"), status: "pending" });
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    const select = await screen.findByLabelText("Model preset");
    expect(await screen.findByRole("option", { name: /Cheapest · anthropic\/claude-haiku-4.5:floor · ≈ \$0.04/ })).toBeInTheDocument();
    await userEvent.selectOptions(select, "strongest");
    await userEvent.click(screen.getByRole("button", { name: /Generate Threat Model/ }));
    expect(trigger).toHaveBeenCalledWith("t1", "s1", "strongest");
  });

  it("a catalog failure only drops the cost labels, the preset select stays", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    vi.spyOn(tmApi, "getRunPresets").mockResolvedValue(OR_PRESETS);
    vi.spyOn(catalogApi, "getModelCatalog").mockRejectedValue(new Error("catalog down"));
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByLabelText("Model preset")).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "Cheapest · anthropic/claude-haiku-4.5:floor" })).toBeInTheDocument();
  });

  it("no costs for a direct provider, and Default sends no preset", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    vi.spyOn(tmApi, "getRunPresets").mockResolvedValue({
      credential_provider: "anthropic",
      presets: { cheapest: "claude-haiku-4-5-20251001", balanced: "claude-sonnet-4-6", strongest: "claude-opus-5-5" },
    });
    const trigger = vi.spyOn(api, "triggerThreatModel").mockResolvedValue({ ...failed("x"), status: "pending" });
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByRole("option", { name: "Strongest · claude-opus-5-5" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Generate Threat Model/ }));
    expect(trigger).toHaveBeenCalledWith("t1", "s1", undefined);
  });

  it("a finished run shows its own call's facts instead of the old token total", async () => {
    vi.spyOn(api, "getThreatModel").mockResolvedValue({
      ...failed(""), status: "done", error: null, provider: "openrouter", model: "anthropic/claude-sonnet-4.6",
      prompt_tokens: 18203, completion_tokens: 3941, threats: [],
      last_call: {
        unavailable: false, provider: "openrouter", model: "anthropic/claude-sonnet-4.6", key_source: "team",
        status: "ok", prompt_tokens: 18203, completion_tokens: 3941, cost_usd: "0.0729", latency_ms: 41200,
        finish_reason: "stop",
      },
    });
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByText("anthropic/claude-sonnet-4.6 · team key · 18,203 → 3,941 tokens · $0.07 · 41 s")).toBeInTheDocument();
    expect(screen.queryByText(/22,144 tokens/)).not.toBeInTheDocument();
  });

  it("a failed run shows what the failed call consumed; a run that never called the LLM shows nothing", async () => {
    vi.spyOn(api, "getThreatModel").mockResolvedValueOnce({
      ...failed("OpenRouter model 'x' hit max_tokens before finishing"),
      last_call: {
        unavailable: false, provider: "openrouter", model: "x", key_source: "team", status: "error",
        prompt_tokens: 21480, completion_tokens: 12000, cost_usd: "0.41", latency_ms: 123000, finish_reason: "length",
      },
    });
    const { unmount } = render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByText("x · team key · 21,480 → 12,000 tokens · $0.41 · 2 min 3 s")).toBeInTheDocument();
    unmount();

    vi.spyOn(api, "getThreatModel").mockResolvedValueOnce({ ...failed("no LLM API key: save one"), last_call: null });
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    await screen.findByText(/no LLM API key/);
    expect(screen.queryByText(/tokens/)).not.toBeInTheDocument();
  });

  it("names the default model and warns when a preset replaces it, then resets after a run", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(new Error("404"));
    vi.spyOn(tmApi, "getRunPresets").mockResolvedValue({ ...OR_PRESETS, default_model: "deepseek/deepseek-v4-flash-0731", key_source: "team" });
    const trigger = vi.spyOn(api, "triggerThreatModel").mockResolvedValue({ ...failed(""), status: "pending", error: null });
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    const select = await screen.findByLabelText("Model preset");
    expect(await screen.findByRole("option", { name: /^Default · deepseek\/deepseek-v4-flash-0731 \(team key\)/ })).toBeInTheDocument();
    expect(screen.queryByText(/instead of the default/)).not.toBeInTheDocument();
    await userEvent.selectOptions(select, "balanced");
    expect(screen.getByText("Uses anthropic/claude-sonnet-4.6 instead of the default (deepseek/deepseek-v4-flash-0731)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Generate Threat Model/ }));
    expect(trigger).toHaveBeenCalledWith("t1", "s1", "balanced");
    expect(screen.queryByText(/instead of the default/)).not.toBeInTheDocument();
  });

  it("the empty state points to Settings", async () => {
    vi.spyOn(api, "getThreatModel").mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    render(<MemoryRouter><ThreatModelPage sessionId="s1" /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });
});
