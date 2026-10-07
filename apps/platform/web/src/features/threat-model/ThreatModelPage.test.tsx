import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { ThreatModelPage } from "./ThreatModelPage";
import * as api from "../../api/apiClient";
import type { ThreatModelResponse } from "../../api/types";

vi.mock("../../tenant/TenantContext", () => ({ useTenant: () => ({ tenantId: "t1" }) }));

const failed = (error: string): ThreatModelResponse => ({
  status: "failed", provider: null, model: null, prompt_tokens: null, completion_tokens: null,
  analysis_summary: null, error, generated_at: null, updated_at: null,
});

beforeEach(() => { vi.restoreAllMocks(); });

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
});
