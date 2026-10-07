import { useState } from "react";
import { costLabel, PRESET_LABELS, PRESETS, type ModelCatalog, type Preset } from "../../api/llmCatalog";
import { ModelPicker } from "./ModelPicker";
import type { TeamLlmSettings } from "./settingsApi";

export function PresetRows({ settings, catalog, busy, onSave }: {
  settings: TeamLlmSettings;
  catalog: ModelCatalog | null;
  busy: boolean;
  onSave: (presetModels: Record<string, string> | null) => void;
}) {
  const [editing, setEditing] = useState<Preset | null>(null);
  const [draft, setDraft] = useState("");
  const cfg = settings.config;
  const views = settings.presets;
  if (!cfg || !views) return null;
  const overrides = cfg.preset_models ?? {};
  const pickable = cfg.provider === "openrouter" && !!catalog?.available;

  const save = (preset: Preset, model: string | null) => {
    const next: Record<string, string> = { ...overrides };
    if (model) next[preset] = model;
    else delete next[preset];
    setEditing(null);
    onSave(Object.keys(next).length ? next : null);
  };

  return (
    <section className="settings-presets" aria-labelledby="presets-title">
      <h3 id="presets-title" className="settings-subtitle">Presets</h3>
      <p className="settings-hint">Anyone on the team can pick a preset when generating a threat model.</p>
      <ul className="settings-preset-list">
        {PRESETS.map((preset) => {
          const view = views[preset];
          const cost = cfg.provider === "openrouter" ? costLabel(catalog, view.model) : null;
          return (
            <li key={preset} className="settings-preset">
              <span className="settings-preset-name">{PRESET_LABELS[preset]}</span>
              <code>{view.model}</code>
              {/* Runs append ":floor" on OpenRouter (see the Threat Model preset select). */}
              {cfg.provider === "openrouter" && preset === "cheapest" && !view.model.includes(":") && (
                <span className="settings-hint">routes to the cheapest host</span>
              )}
              <span className="settings-badge">{view.source === "team" ? "team" : "default"}</span>
              {view.available === false && <span className="settings-error">not in catalog</span>}
              {cost && <span>{cost}</span>}
              {settings.can_edit && (
                <span className="settings-actions">
                  <button type="button" className="shell-btn" disabled={busy}
                    onClick={() => { setDraft(view.model); setEditing(preset); }}>Edit</button>
                  {view.source === "team" && (
                    <button type="button" className="shell-btn" disabled={busy} onClick={() => save(preset, null)}>Reset</button>
                  )}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {editing && pickable && catalog && (
        <ModelPicker catalog={catalog} title={`${PRESET_LABELS[editing]} model`}
          onPick={(id) => save(editing, id)} onClose={() => setEditing(null)} />
      )}
      {editing && !pickable && (
        <form className="settings-form" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) save(editing, draft.trim()); }}>
          <label>
            {PRESET_LABELS[editing]} model
            <input value={draft} onChange={(e) => setDraft(e.target.value)} />
          </label>
          <div className="settings-actions">
            <button type="submit" className="btn-primary" disabled={busy || !draft.trim()}>Save preset</button>
            <button type="button" className="shell-btn" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
}
