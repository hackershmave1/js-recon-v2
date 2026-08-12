import { useEffect, useState } from "react";
import type React from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { getRunConfig, editAndRerun, ApiError } from "../../api/apiClient";
import type { RunConfig } from "../../api/types";
import "./newRun.css";

const MIB = 1024 * 1024;

// Normalized key for a scope change-comparison (order/case/trailing-dot insensitive),
// mirroring the backend so re-submitting the PREFILLED scope isn't seen as a change.
function scopeKey(hosts: string[]): string {
  return [...new Set(hosts.map((h) => h.trim().toLowerCase().replace(/^\*\./, "").replace(/\.$/, "")).filter(Boolean))]
    .sort()
    .join(",");
}

// Approximate the backend's egress.host_in_scope (exact host or dot-boundary subdomain)
// so the UI can gate the ack. The backend stays authoritative — this only decides whether
// to prompt for re-attestation before the round-trip.
function hostInScope(target: string, scope: string[]): boolean {
  const host = target.trim().toLowerCase().replace(/^https?:\/\//, "").split("/")[0].split(":")[0].replace(/\.$/, "");
  if (!host) return true;
  return scope.some((s) => {
    const e = s.trim().toLowerCase().replace(/\.$/, "");
    return e !== "" && (host === e || host.endsWith("." + e));
  });
}

// A prefilled form to edit a finished run's config and launch a new run inheriting it.
// The source run is never mutated (runs are immutable). Reuses the New Recon form styles.
export function EditRerunPanel({ runId, onCancel }: { runId: string; onCancel: () => void }) {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [cfg, setCfg] = useState<RunConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [capture, setCapture] = useState(false);
  const [scopeHosts, setScopeHosts] = useState<string[]>([]);
  const [scopeInput, setScopeInput] = useState("");
  const [capMiB, setCapMiB] = useState("");
  const [authorizedBy, setAuthorizedBy] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let active = true;
    getRunConfig(tenantId, runId)
      .then((c) => {
        if (!active) return;
        setCfg(c);
        setTarget(c.target ?? "");
        setCapture(c.crawl_mode === "capture");
        setScopeHosts(c.scope_hosts);
        setCapMiB(c.max_fetch_bytes ? String(Math.round(c.max_fetch_bytes / MIB)) : "");
        // MF1: the authorization ack is NEVER prefilled — a scope change is re-attested.
      })
      .catch((e) => {
        if (active) setLoadError(e instanceof ApiError ? e.message : "Failed to load run config");
      });
    return () => { active = false; };
  }, [tenantId, runId]);

  const isUpload = cfg?.is_upload ?? false;
  // Fold a typed-but-not-yet-added host into the scope so a value left in the input isn't
  // silently dropped (matches NewRunPanel).
  const pending = scopeInput.trim();
  const hosts = pending && !scopeHosts.includes(pending) ? [...scopeHosts, pending] : scopeHosts;
  // The edit forks a fresh session (needing a fresh ack) when the scope changed OR a crawl
  // target moved outside the current scope — exactly the backend's fork triggers (MF3).
  const scopeChanged = cfg != null && scopeKey(hosts) !== scopeKey(cfg.scope_hosts);
  const targetLeftScope = cfg != null && !isUpload && target.trim() !== "" && !hostInScope(target, cfg.scope_hosts);
  const needsAck = scopeChanged || targetLeftScope;

  const targetReady = isUpload || target.trim() !== "";
  const ready = cfg != null && targetReady && (!needsAck || authorizedBy.trim() !== "") && !busy;

  function addHost() {
    const host = scopeInput.trim();
    if (host === "") return;
    setScopeHosts((prev) => (prev.includes(host) ? prev : [...prev, host]));
    setScopeInput("");
  }
  function onScopeKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") { e.preventDefault(); addHost(); }  // add, don't submit
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId) return;
    setBusy(true); setError(null);
    try {
      const body: {
        target?: string; capture?: boolean; scope_hosts?: string[];
        authorized_by?: string; max_fetch_bytes?: number;
      } = { scope_hosts: hosts };
      if (target.trim()) body.target = target.trim();
      if (!isUpload) {
        body.capture = capture;
        const mib = Number(capMiB);
        if (capMiB.trim() !== "" && mib > 0) body.max_fetch_bytes = Math.round(mib * MIB);
      }
      if (authorizedBy.trim()) body.authorized_by = authorizedBy.trim();
      const run = await editAndRerun(tenantId, runId, body);
      navigate(`/runs/${run.run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to re-run");
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <div className="card nr-form"><p className="sev-high">{loadError}</p></div>;
  if (!cfg) return <div className="card nr-form"><p className="muted">Loading run config…</p></div>;

  return (
    <form className="card nr-form" onSubmit={submit}>
      <h2 className="nr-title">Edit &amp; re-run</h2>
      <p className="muted nr-sub">
        Launches a new run inheriting this run&apos;s config with your edits — the original run is untouched.
      </p>

      <div className="nr-field">
        <label htmlFor="rr-target">{isUpload ? "Target (base-URL hint)" : "Domain"}</label>
        <input id="rr-target" value={target} placeholder="acme.io"
          onChange={(e) => setTarget(e.target.value)} />
      </div>

      {!isUpload && (
        <>
          <label className="nr-capture">
            <input type="checkbox" checked={capture} onChange={(e) => setCapture(e.target.checked)} />
            <span className="nr-capture-text">
              <span className="nr-capture-title">Runtime capture <span className="nr-beta">beta</span></span>
              <span className="muted nr-capture-hint">Execute the page in a headless browser to capture runtime JS. Must be enabled server-side.</span>
            </span>
          </label>
          <div className="nr-field">
            <label htmlFor="rr-cap">Fetch size cap (MiB)</label>
            <input id="rr-cap" type="number" min="1" value={capMiB} placeholder="default"
              onChange={(e) => setCapMiB(e.target.value)} />
            <p className="muted nr-hint">Per-run override for a large bundle (over the 10 MiB default). Blank = default.</p>
          </div>
        </>
      )}

      <div className="nr-field">
        <label htmlFor="rr-scope">Scope hosts</label>
        {scopeHosts.length > 0 && (
          <ul className="nr-chips" aria-label="Scope hosts">
            {scopeHosts.map((host) => (
              <li key={host} className="chip nr-chip">
                <span className="nr-chip-label">{host}</span>
                <button type="button" className="nr-chip-x" aria-label={`Remove ${host}`}
                  onClick={() => setScopeHosts((prev) => prev.filter((h) => h !== host))}>×</button>
              </li>
            ))}
          </ul>
        )}
        <div className="nr-scope-add">
          <input id="rr-scope" value={scopeInput} placeholder="example.com  (or *.example.com)"
            onChange={(e) => setScopeInput(e.target.value)} onKeyDown={onScopeKeyDown} />
          <button type="button" className="nr-add" onClick={addHost} disabled={scopeInput.trim() === ""}>Add</button>
        </div>
      </div>

      {needsAck && (
        <div className="nr-field">
          <p className="sev-high nr-hint">
            This starts a fresh session (new scope) — triage history and any attached spec won&apos;t carry
            over, and you must re-attest authorization for the new scope.
          </p>
          <label htmlFor="rr-auth">Authorized by</label>
          <input id="rr-auth" value={authorizedBy} placeholder="your name / ticket"
            onChange={(e) => setAuthorizedBy(e.target.value)} />
        </div>
      )}

      {error && <p className="sev-high nr-error">{error}</p>}
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button type="button" className="nr-add" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="btn-primary nr-submit" disabled={!ready}>
          {busy ? "Starting…" : "Re-run"}
        </button>
      </div>
    </form>
  );
}
