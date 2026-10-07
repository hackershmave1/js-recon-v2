import { describe, it, expect } from "vitest";
import { costLabel, findModel, formatCost, type ModelCatalog } from "./llmCatalog";

const CATALOG: ModelCatalog = {
  available: true, stale: false, fetched_at: null,
  estimate: { prompt_tokens: 20000, completion_tokens: 4000, basis: "assumed", runs: 0 },
  models: [
    { id: "anthropic/claude-haiku-4.5", name: "Claude Haiku 4.5", context_length: 200000, max_completion_tokens: 64000, prompt_price: "0.000001", completion_price: "0.000005" },
    { id: "apodex/apodex-1.1-mini:free", name: "Apodex Mini (free)", context_length: 262144, max_completion_tokens: null, prompt_price: "0", completion_price: "0" },
  ],
};

describe("llmCatalog", () => {
  it("formats cost", () => {
    expect(formatCost(0)).toBe("free");
    expect(formatCost(0.004)).toBe("<$0.01");
    expect(formatCost(0.123)).toBe("≈ $0.12");
  });
  it("finds a routing variant by its base id, and a catalog variant as-is", () => {
    expect(findModel(CATALOG, "anthropic/claude-haiku-4.5:floor")?.id).toBe("anthropic/claude-haiku-4.5");
    expect(findModel(CATALOG, "apodex/apodex-1.1-mini:free")?.id).toBe("apodex/apodex-1.1-mini:free");
    expect(findModel(CATALOG, "nope/model")).toBeUndefined();
  });
  it("labels cost per threat model from the estimate", () => {
    // 20000 × 0.000001 + 4000 × 0.000005 = 0.04
    expect(costLabel(CATALOG, "anthropic/claude-haiku-4.5:floor")).toBe("≈ $0.04");
    expect(costLabel(null, "anthropic/claude-haiku-4.5")).toBeNull();
  });
});
