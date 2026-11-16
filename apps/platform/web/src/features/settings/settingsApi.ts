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

export interface TeamRule {
  id: string;
  kind: "host" | "file_pattern";
  value: string;
  created_at: string | null;
  created_by: string | null;
}

export interface ThirdPartyRules {
  builtin: { hosts: Record<string, string[]>; file_patterns: string[] };
  team: { hosts: TeamRule[]; file_patterns: TeamRule[] };
  limits: { hosts: number; file_patterns: number; pattern_length: number };
  can_edit: boolean;
}

export function getThirdPartyRules(tenantId: string): Promise<ThirdPartyRules> {
  return request("/settings/third-party-rules", {}, tenantId);
}

export function addThirdPartyRule(tenantId: string, kind: TeamRule["kind"], value: string): Promise<TeamRule> {
  return request("/settings/third-party-rules", json("POST", { kind, value }), tenantId);
}

export function removeThirdPartyRule(tenantId: string, id: string): Promise<void> {
  return request(`/settings/third-party-rules/${encodeURIComponent(id)}`, { method: "DELETE" }, tenantId);
}
