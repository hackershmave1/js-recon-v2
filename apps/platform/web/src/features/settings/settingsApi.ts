import { json, request } from "../../api/apiClient";

export interface TeamLlmConfig {
  provider: string;
  model: string;
  has_key: boolean;
  configured_at: string | null;
  configured_by: string | null;
  tested_at: string | null;
}

export interface TeamLlmSettings {
  config: TeamLlmConfig | null;
  can_edit: boolean;
  default_models: Record<string, string>;
  providers: string[];
}

export function getTeamLlmSettings(tenantId: string): Promise<TeamLlmSettings> {
  return request("/settings/llm", {}, tenantId);
}

export function saveTeamLlmSettings(
  tenantId: string,
  body: { provider: string; model: string; api_key: string },
): Promise<TeamLlmConfig> {
  return request("/settings/llm", json("PUT", body), tenantId);
}

export function deleteTeamLlmSettings(tenantId: string): Promise<void> {
  return request("/settings/llm", { method: "DELETE" }, tenantId);
}

export function testTeamLlmSettings(tenantId: string): Promise<{ ok: boolean; provider: string; model: string }> {
  return request("/settings/llm/test", { method: "POST" }, tenantId);
}
