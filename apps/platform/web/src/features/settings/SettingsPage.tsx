import { useCallback, useEffect, useState } from "react";
import { getModelCatalog, type ModelCatalog } from "../../api/llmCatalog";
import { ConfirmModal } from "../../shell/ConfirmModal";
import { ModelPicker } from "./ModelPicker";
import { PresetRows } from "./PresetRows";
import {
  deleteTeamLlmSettings, getTeamLlmSettings, saveTeamLlmSettings, testTeamLlmSettings,
  type TeamLlmSettings,
} from "./settingsApi";
import "./settings.css";

type Status = { kind: "ok" | "error"; text: string };

const when = (iso: string) => new Date(iso).toLocaleString();

export function SettingsPage({ tenantId }: { tenantId: string }) {
  const [settings, setSettings] = useState<TeamLlmSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [pickingModel, setPickingModel] = useState(false);

  const reload = useCallback(async () => {
    const next = await getTeamLlmSettings(tenantId);
    const chosen = next.config?.provider ?? next.providers[0] ?? "";
    setSettings(next);
    setProvider(chosen);
    setModel(next.config?.model ?? next.default_models[chosen] ?? "");
    setApiKey(""); // never keep a key in the DOM after a round-trip
  }, [tenantId]);

  useEffect(() => {
    reload().catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, [reload]);

  useEffect(() => {
    // Separate from the settings load so an unreachable OpenRouter never delays the page.
    // Once the catalog is cached, refresh just the preset availability flags.
    getModelCatalog(tenantId)
      .then((c) => {
        setCatalog(c);
        if (!c.available) return undefined; // nothing cached to refresh against
        return getTeamLlmSettings(tenantId).then((s) =>
          setSettings((prev) => (prev ? { ...prev, presets: s.presets } : prev)),
        );
      })
      .catch(() => setCatalog(null));
  }, [tenantId]);

  async function run(action: () => Promise<unknown>, okText: string) {
    setBusy(true);
    setStatus(null);
    try {
      await action();
      await reload();
      setStatus({ kind: "ok", text: okText });
    } catch (e) {
      setStatus({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <section className="card settings-card"><p className="settings-error">{loadError}</p></section>;
  if (!settings) return <section className="card settings-card"><p>Loading…</p></section>;

  const cfg = settings.config;
  // A saved key belongs to its provider; switching provider needs a new key (the API 422s).
  const keepsSavedKey = !!cfg?.has_key && provider === cfg.provider;
  return (
    <section className="card settings-card" aria-labelledby="team-llm-title">
      <h2 id="team-llm-title" className="rp-title">Team LLM provider</h2>
      <p className="settings-hint">
        Used for threat models in every session of this team. A key saved on a session overrides it.
      </p>

      {settings.can_edit ? (
        <form
          className="settings-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => saveTeamLlmSettings(tenantId, { provider, model: model.trim(), api_key: apiKey }), "Saved");
          }}
        >
          <label>
            Provider
            <select
              value={provider}
              onChange={(e) => { setProvider(e.target.value); setModel(settings.default_models[e.target.value] ?? ""); }}
            >
              {settings.providers.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label>
            Model
            <input value={model} onChange={(e) => setModel(e.target.value)} />
          </label>
          {provider === "openrouter" && catalog?.available && (
            <button type="button" className="shell-btn" onClick={() => setPickingModel(true)}>Choose from catalog…</button>
          )}
          {pickingModel && catalog && (
            <ModelPicker catalog={catalog} title="Team default model"
              onPick={(id) => { setModel(id); setPickingModel(false); }} onClose={() => setPickingModel(false)} />
          )}
          <label>
            API key
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                keepsSavedKey ? "A key is saved. Leave blank to keep it."
                  : cfg?.has_key ? "Enter a key for this provider" : "Paste your API key"
              }
            />
          </label>
          <div className="settings-actions">
            <button type="submit" className="btn-primary" disabled={busy || !model.trim() || (!keepsSavedKey && !apiKey)}>
              Save
            </button>
            <button type="button" className="shell-btn" disabled={busy || !cfg?.has_key}
              onClick={() => void run(() => testTeamLlmSettings(tenantId), "Key works")}>
              Test
            </button>
            <button type="button" className="shell-btn" disabled={busy || !cfg}
              onClick={() => setConfirmRemove(true)}>
              Remove
            </button>
          </div>
        </form>
      ) : (
        <>
          <dl className="settings-readonly">
            <dt>Provider</dt><dd>{cfg?.provider ?? "Not set"}</dd>
            <dt>Model</dt><dd>{cfg?.model ?? "—"}</dd>
            <dt>Key</dt><dd>{cfg?.has_key ? "Saved" : "Not set"}</dd>
          </dl>
          <p className="settings-hint">Only admins can change the team key.</p>
        </>
      )}

      {cfg && (
        <p className="settings-meta">
          Last saved {cfg.configured_at ? when(cfg.configured_at) : "—"}
          {cfg.configured_by ? ` by ${cfg.configured_by}` : ""}
          {cfg.tested_at ? ` · tested ${when(cfg.tested_at)}` : ""}
        </p>
      )}
      <PresetRows
        settings={settings}
        catalog={catalog}
        busy={busy}
        onSave={(presetModels) => {
          if (!cfg) return;
          // The saved provider/model, not unsaved form edits: this PUT changes only presets.
          void run(
            () => saveTeamLlmSettings(tenantId, { provider: cfg.provider, model: cfg.model, api_key: "", preset_models: presetModels }),
            "Presets saved",
          );
        }}
      />
      {status && <p role="status" className={status.kind === "ok" ? "settings-ok" : "settings-error"}>{status.text}</p>}

      {confirmRemove && (
        <ConfirmModal
          title="Remove the team LLM key?"
          message="Sessions without their own key will stop generating threat models."
          confirmLabel="Remove"
          danger
          onConfirm={() => { setConfirmRemove(false); void run(() => deleteTeamLlmSettings(tenantId), "Removed"); }}
          onCancel={() => setConfirmRemove(false)}
        />
      )}
    </section>
  );
}
