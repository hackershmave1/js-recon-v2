import { useCallback, useEffect, useState } from "react";
import {
  addThirdPartyRule, getThirdPartyRules, removeThirdPartyRule,
  type TeamRule, type ThirdPartyRules,
} from "./settingsApi";
import "./settings.css";

type Status = { kind: "ok" | "error"; text: string };

// Settings > Third-party rules (ADR-0010). The built-in list is read-only; the team's additions apply
// to every session and the extension downloads them. They only ever ADD to what is dropped.
export function ThirdPartyRulesCard({ tenantId }: { tenantId: string }) {
  const [rules, setRules] = useState<ThirdPartyRules | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [host, setHost] = useState("");
  const [pattern, setPattern] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => setRules(await getThirdPartyRules(tenantId)), [tenantId]);
  useEffect(() => {
    reload().catch(() => setLoadError(true));
  }, [reload]);

  async function run(action: () => Promise<unknown>, okText: string, done: () => void) {
    setBusy(true);
    setStatus(null);
    try {
      await action();
      done();
      await reload();
      setStatus({ kind: "ok", text: okText });
    } catch (e) {
      setStatus({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <section className="card settings-card"><h2 className="rp-title">Third-party rules</h2><p className="muted">Could not load the third-party rules.</p></section>;
  if (!rules) return <section className="card settings-card"><h2 className="rp-title">Third-party rules</h2><p className="muted">Loading…</p></section>;

  const builtinHostCount = Object.values(rules.builtin.hosts).flat().length;
  const editable = rules.can_edit;
  const teamRow = (rule: TeamRule) => (
    <li key={rule.id} className="settings-preset">
      <code>{rule.value}</code>
      {rule.created_by && <span className="muted">added by {rule.created_by}</span>}
      {editable && (
        <button type="button" className="btn" disabled={busy} aria-label={`Remove ${rule.value}`}
          onClick={() => void run(() => removeThirdPartyRule(tenantId, rule.id), "Removed", () => undefined)}>
          Remove
        </button>
      )}
    </li>
  );

  return (
    <section className="card settings-card" aria-labelledby="third-party-title">
      <h2 id="third-party-title" className="rp-title">Third-party rules</h2>
      <p className="settings-hint">
        Scripts that match a rule are dropped by the extension and hidden in findings, for every session.
        A host rule never drops a host you typed into a session's scope; a file pattern applies on any host.
        Your additions only add to the built-in list: nothing here can switch a built-in rule off.
      </p>

      <h3 className="settings-subtitle">Your team's additions</h3>
      {rules.team.hosts.length + rules.team.file_patterns.length === 0 && <p className="muted">None yet.</p>}
      {rules.team.hosts.length > 0 && <ul className="settings-preset-list" aria-label="Team hosts">{rules.team.hosts.map(teamRow)}</ul>}
      {rules.team.file_patterns.length > 0 && <ul className="settings-preset-list" aria-label="Team file patterns">{rules.team.file_patterns.map(teamRow)}</ul>}

      {editable && (
        <div className="settings-form">
          <form onSubmit={(e) => { e.preventDefault(); void run(() => addThirdPartyRule(tenantId, "host", host.trim()), "Added", () => setHost("")); }}>
            <label>
              Host
              <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="tracker.example.com" maxLength={253} />
            </label>
            <button type="submit" className="btn" disabled={busy || !host.trim()}>Add host</button>
          </form>
          <form onSubmit={(e) => { e.preventDefault(); void run(() => addThirdPartyRule(tenantId, "file_pattern", pattern.trim()), "Added", () => setPattern("")); }}>
            <label>
              File pattern
              <input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="/vendor/chat-widget*.js" maxLength={rules.limits.pattern_length} />
            </label>
            <button type="submit" className="btn" disabled={busy || !pattern.trim()}>Add pattern</button>
          </form>
        </div>
      )}
      {status && <p role={status.kind === "ok" ? "status" : "alert"} className={status.kind === "ok" ? "settings-ok" : "settings-error"}>{status.text}</p>}

      <section aria-label="Built-in rules">
        <h3 className="settings-subtitle">Built-in rules (read-only)</h3>
        <details>
          <summary>{builtinHostCount} hosts</summary>
          {Object.entries(rules.builtin.hosts).map(([group, hosts]) => (
            <p key={group} className="settings-meta"><strong>{group.replace(/_/g, " ")}:</strong> {hosts.join(", ")}</p>
          ))}
        </details>
        <details>
          <summary>{rules.builtin.file_patterns.length} file patterns</summary>
          <ul className="settings-preset-list">
            {rules.builtin.file_patterns.map((p) => <li key={p}><code>{p}</code></li>)}
          </ul>
        </details>
      </section>
    </section>
  );
}
