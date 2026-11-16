import { useCallback, useEffect, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import {
  addScopeHost, decideHost, deleteHostContent, getSessionScope, removeScopeHost,
} from "../../api/apiClient";
import type { SessionScope } from "../../api/types";
import "./scope-panel.css";

// The session's scope, where the researcher reads the results (ADR-0010, S3): what is in scope,
// which hosts are waiting for a decision (their scripts are held, not analyzed), which are ignored,
// and which were removed and are therefore hidden. Editing scope is read-time: adding brings a
// host's content in, removing HIDES it (reversible), and deleting a removed host's content is a
// separate, explicit and irreversible action behind its own confirmation.

const DAY_MS = 24 * 60 * 60 * 1000;

// "5 days left", "today", "expired": the 7-day countdown before held scripts are deleted.
export function expiryLabel(expiresAt: string | null, now: number = Date.now()): string {
  if (!expiresAt) return "";
  const left = Date.parse(expiresAt) - now;
  if (Number.isNaN(left)) return "";
  if (left <= 0) return "expired";
  const days = Math.ceil(left / DAY_MS);
  return days <= 1 ? "deleted within a day" : `deleted in ${days} days`;
}

export function ScopePanel({ sessionId, onChanged }: { sessionId: string; onChanged?: () => void }) {
  const { tenantId } = useTenant();
  const [view, setView] = useState<SessionScope | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!tenantId) return;
    try {
      setView(await getSessionScope(tenantId, sessionId));
      setError(null);
    } catch {
      setError("Couldn't load this session's scope.");
    }
  }, [tenantId, sessionId]);

  useEffect(() => { void load(); }, [load]);

  // Run one change, then re-read the panel and tell the page so its hosts and findings re-label.
  const act = async (key: string, fn: () => Promise<unknown>, failure: string) => {
    if (!tenantId) return;
    setBusy(key);
    try {
      await fn();
      await load();
      onChanged?.();
    } catch {
      setError(failure);
    } finally {
      setBusy(null);
      setConfirmDelete(null);
    }
  };

  if (!view) {
    return error ? <div className="card sp"><p className="sp-error" role="alert">{error}</p></div> : null;
  }
  const add = (host: string) => act(`add:${host}`, () => addScopeHost(tenantId!, sessionId, host), `Couldn't add ${host}.`);

  return (
    <div className="card sp">
      <h2 className="rp-title">Scope</h2>
      <p className="sp-note muted">
        What this session treats as the target. Adding a host brings its content in; removing one hides its files and
        findings under “Hidden: out of scope” and they return if you add it back.
      </p>
      {error && <p className="sp-error" role="alert">{error}</p>}

      <ul className="sp-chips" aria-label="Hosts in scope">
        {view.scope_hosts.length === 0 && <li className="muted">No scope set.</li>}
        {view.scope_hosts.map((host) => (
          <li key={host} className="sp-chip">
            <span>{host}</span>
            <button type="button" aria-label={`Remove ${host} from scope`} disabled={busy !== null}
              onClick={() => act(`rm:${host}`, () => removeScopeHost(tenantId!, sessionId, host), `Couldn't remove ${host}.`)}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <form className="sp-add" aria-label="Add a host" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) { void add(draft.trim()); setDraft(""); } }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="acme.io" aria-label="Add a host to the scope" />
        <button type="submit" disabled={busy !== null || !draft.trim()}>Add to scope</button>
      </form>

      {view.undecided.length > 0 && (
        <section className="sp-group" aria-label="Waiting for your decision">
          <h3>Waiting for your decision</h3>
          <p className="sp-note muted">Their scripts are held, not analyzed. Add widens the scope to the host and its subdomains.</p>
          <ul>
            {view.undecided.map((u) => (
              <li key={u.host} className="sp-row">
                <span className="sp-host">{u.host}</span>
                <span className="muted">
                  {u.files} script{u.files === 1 ? "" : "s"}{u.expires_at ? ` · ${expiryLabel(u.expires_at)}` : ""}
                </span>
                <span className="sp-actions">
                  <button type="button" disabled={busy !== null}
                    onClick={() => act(`d:${u.host}`, () => decideHost(tenantId!, sessionId, u.host, "add"), `Couldn't add ${u.host}.`)}>
                    Add
                  </button>
                  <button type="button" disabled={busy !== null}
                    onClick={() => act(`i:${u.host}`, () => decideHost(tenantId!, sessionId, u.host, "ignore"), `Couldn't ignore ${u.host}.`)}>
                    Ignore
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {view.ignored.length > 0 && (
        <section className="sp-group" aria-label="Ignored hosts">
          <h3>Ignored</h3>
          <p className="sp-note muted">Their scripts are never fetched for this session.</p>
          <ul>
            {view.ignored.map((host) => (
              <li key={host} className="sp-row">
                <span className="sp-host">{host}</span>
                <span className="sp-actions">
                  <button type="button" disabled={busy !== null} onClick={() => add(host)}>Add to scope</button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {view.hidden.length > 0 && (
        <section className="sp-group" aria-label="Hidden: out of scope">
          <h3>Hidden: out of scope</h3>
          <p className="sp-note muted">Removed from the scope. Hidden, not deleted: add one back and it returns.</p>
          <ul>
            {view.hidden.map((h) => (
              <li key={h.host} className="sp-row">
                <span className="sp-host">{h.host}</span>
                <span className="muted">
                  {h.files} file{h.files === 1 ? "" : "s"} · {h.findings} finding{h.findings === 1 ? "" : "s"}
                </span>
                <span className="sp-actions">
                  <button type="button" disabled={busy !== null} onClick={() => add(h.host)}>Add back</button>
                  {confirmDelete === h.host ? (
                    <span className="sp-confirm" role="group" aria-label={`Confirm deleting ${h.host}`}>
                      <span>Delete {h.files} file{h.files === 1 ? "" : "s"} and the findings seen only in {h.files === 1 ? "it" : "them"}? This can't be undone.</span>
                      <button type="button" className="sp-danger" disabled={busy !== null}
                        onClick={() => act(`del:${h.host}`, () => deleteHostContent(tenantId!, sessionId, h.host), `Couldn't delete ${h.host}.`)}>
                        Delete
                      </button>
                      <button type="button" onClick={() => setConfirmDelete(null)}>Cancel</button>
                    </span>
                  ) : (
                    <button type="button" className="sp-danger" disabled={busy !== null} onClick={() => setConfirmDelete(h.host)}>
                      Delete content
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
