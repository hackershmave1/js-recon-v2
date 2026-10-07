import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelPicker } from "./ModelPicker";
import type { ModelCatalog } from "../../api/llmCatalog";

const CATALOG: ModelCatalog = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 10000, completion_tokens: 2000, basis: "history", runs: 3 },
  models: [
    { id: "anthropic/claude-opus-5.5", name: "Claude Opus 5.5", context_length: 1000000, max_completion_tokens: 128000, prompt_price: "0.000004", completion_price: "0.00002" },
    { id: "apodex/apodex-1.1-mini:free", name: "Apodex Mini (free)", context_length: 262144, max_completion_tokens: null, prompt_price: "0", completion_price: "0" },
  ],
};

describe("ModelPicker", () => {
  it("lists cheapest first with costs and says what the estimate is based on", () => {
    render(<ModelPicker catalog={CATALOG} title="Pick" onPick={vi.fn()} onClose={vi.fn()} />);
    const rows = screen.getAllByRole("button", { name: /ctx/ });
    expect(rows[0]).toHaveTextContent("Apodex Mini (free)");
    expect(rows[0]).toHaveTextContent("free");
    expect(rows[1]).toHaveTextContent("≈ $0.08");
    expect(screen.getByText(/based on your last 3 runs/)).toBeInTheDocument();
  });
  it("filters by search and reports the pick", async () => {
    const onPick = vi.fn();
    render(<ModelPicker catalog={CATALOG} title="Pick" onPick={onPick} onClose={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Search models"), "opus");
    const rows = screen.getAllByRole("button", { name: /ctx/ });
    expect(rows).toHaveLength(1);
    await userEvent.click(rows[0]);
    expect(onPick).toHaveBeenCalledWith("anthropic/claude-opus-5.5");
  });
  it("says so when the catalog is unavailable", () => {
    render(<ModelPicker catalog={{ ...CATALOG, available: false, models: [] }} title="Pick" onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(/catalog is unavailable/)).toBeInTheDocument();
  });
});
