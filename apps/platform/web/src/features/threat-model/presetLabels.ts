import { costLabel, type ModelCatalog, type Preset } from "../../api/llmCatalog";
import type { RunPresets } from "./threatModelApi";

const KEY_LABEL: Record<string, string> = { session: "session key", team: "team key", env: "server key" };

// The menu's no-preset entry names the model that will actually run (chosen in Settings or on the
// session), so picking a preset is a visible choice rather than a silent override. The key is
// named separately: the model's and the key's owner can differ (e.g. server key + session model).
export function defaultOptionLabel(menu: RunPresets | null, catalog: ModelCatalog | null): string {
  if (!menu?.default_model) return "Default model";
  const cost = costLabel(catalog, menu.default_model);
  const key = menu.key_source ? ` (${KEY_LABEL[menu.key_source] ?? menu.key_source})` : "";
  return `Default · ${menu.default_model}${key}${cost ? ` · ${cost}` : ""}`;
}

export function overrideNote(menu: RunPresets | null, preset: Preset | ""): string | null {
  if (!preset || !menu?.presets || !menu.default_model) return null;
  const model = menu.presets[preset];
  if (model === menu.default_model) return null;
  return `Uses ${model} instead of the default (${menu.default_model})`;
}
