import { json, request } from "../../api/apiClient";
import type { Preset } from "../../api/llmCatalog";

export interface TeamLlmConfig {
  provider: string;
  model: string;
  has_key: boolean;
  configured_at: string | null;
  configured_by: string | null;
  tested_at: string | null;
  preset_models: Record<string, string>;
}

export interface PresetView { model: string; source: "team" | "builtin"; available: boolean | null }

export interface TeamLlmSettings {
  config: TeamLlmConfig | null;
  can_edit: boolean;
  default_models: Record<string, string>;
  providers: string[];
  presets: Record<Preset, PresetView> | null;
  builtin_preset_models: Record<string, Record<string, string>>;
}

export function getTeamLlmSettings(tenantId: string): Promise<TeamLlmSettings> {
  return request("/settings/llm", {}, tenantId);
}

export function saveTeamLlmSettings(
  tenantId: string,
  body: { provider: string; model: string; api_key: string; preset_models?: Record<string, string> | null },
): Promise<TeamLlmConfig> {
  return request("/settings/llm", json("PUT", body), tenantId);
}

export function deleteTeamLlmSettings(tenantId: string): Promise<void> {
  return request("/settings/llm", { method: "DELETE" }, tenantId);
}

export function testTeamLlmSettings(tenantId: string): Promise<{ ok: boolean; provider: string; model: string }> {
  return request("/settings/llm/test", { method: "POST" }, tenantId);
}
