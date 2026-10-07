import { request } from "./apiClient";

// Shared by Settings and the Threat Model tab (features don't import each other).
export const PRESETS = ["cheapest", "balanced", "strongest"] as const;
export type Preset = (typeof PRESETS)[number];
export const PRESET_LABELS: Record<Preset, string> = { cheapest: "Cheapest", balanced: "Balanced", strongest: "Strongest" };

export interface CatalogModel {
  id: string;
  name: string;
  context_length: number;
  max_completion_tokens: number | null;
  prompt_price: string; // USD per token
  completion_price: string;
}
export interface CostEstimate { prompt_tokens: number; completion_tokens: number; basis: "history" | "assumed"; runs: number }
export interface ModelCatalog { available: boolean; stale: boolean; fetched_at: string | null; models: CatalogModel[]; estimate: CostEstimate }

export function getModelCatalog(tenantId: string): Promise<ModelCatalog> {
  return request("/settings/llm/models", {}, tenantId);
}

export function costPerRun(m: CatalogModel, est: CostEstimate): number {
  return Number(m.prompt_price) * est.prompt_tokens + Number(m.completion_price) * est.completion_tokens;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "free";
  if (usd < 0.01) return "<$0.01";
  return `≈ $${usd.toFixed(2)}`;
}

// Routing variants (":floor") aren't catalog entries; catalog variants (":free") are.
export function findModel(catalog: ModelCatalog | null, id: string): CatalogModel | undefined {
  if (!catalog) return undefined;
  return catalog.models.find((m) => m.id === id) ?? catalog.models.find((m) => m.id === id.split(":")[0]);
}

export function costLabel(catalog: ModelCatalog | null, id: string): string | null {
  const model = findModel(catalog, id);
  return model && catalog ? formatCost(costPerRun(model, catalog.estimate)) : null;
}
