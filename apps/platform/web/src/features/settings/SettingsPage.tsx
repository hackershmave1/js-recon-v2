import { useCallback, useEffect, useState } from "react";
import { ConfirmModal } from "../../shell/ConfirmModal";
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
          <label>
            API key
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={cfg?.has_key ? "A key is saved. Leave blank to keep it." : "Paste your API key"}
            />
          </label>
          <div className="settings-actions">
            <button type="submit" className="btn-primary" disabled={busy || !model.trim() || (!cfg?.has_key && !apiKey)}>
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
