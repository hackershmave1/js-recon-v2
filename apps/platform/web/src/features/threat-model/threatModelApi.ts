import { request } from "../../api/apiClient";
import type { Preset } from "../../api/llmCatalog";

export interface RunPresets {
  credential_provider: string | null;
  // What a run with no preset uses (the model chosen in Settings / on the session), and whose key.
  default_model?: string | null;
  key_source?: "session" | "team" | "env" | null;
  presets: Record<Preset, string> | null;
}

// What each preset would actually run for this session's credential (no key material).
export function getRunPresets(tenantId: string, sessionId: string): Promise<RunPresets> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/threat-model/presets`, {}, tenantId);
}
