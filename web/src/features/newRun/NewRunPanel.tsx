import { useState } from "react";
import type React from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { createSession, startRun, uploadRun } from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";
import "./newRun.css";

export function NewRunPanel() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"upload" | "crawl">("upload");
  const [scopeHosts, setScopeHosts] = useState<string[]>([]);
  const [scopeInput, setScopeInput] = useState("");
  const [authorizedBy, setAuthorizedBy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Scope is optional now (S4): a crawl with no scope defaults to the target's
  // host + subdomains server-side (S3); an upload needs no scope at all. So the
  // gate is just the authorization ack plus the mode's own input.
  const targetReady = mode === "upload" ? file !== null : domain.trim() !== "";
  const ready = authorizedBy.trim() !== "" && targetReady;

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
    // Fold a typed-but-not-yet-added host into the scope so a value left in the
    // input isn't silently dropped on submit.
    const pending = scopeInput.trim();
    const hosts = pending && !scopeHosts.includes(pending) ? [...scopeHosts, pending] : scopeHosts;
    try {
      // Attach the active engagement (picked in the Sessions engagement switcher) so a
      // new run's session rolls up under it. A blank scope + a crawl target lets the
      // backend seed scope from the domain (S3), so scope can be left empty.
      const engagementId = localStorage.getItem("recon.engagementId");
      const session = await createSession(tenantId, {
        scope_hosts: hosts,
        authorized_by: authorizedBy.trim(),
        ...(mode === "crawl" ? { target: domain.trim() } : {}),
        ...(engagementId ? { engagement_id: engagementId } : {}),
      });
      if (mode === "crawl") {
        const run = await startRun(tenantId, { session_id: session.session_id, target: domain.trim() });
        navigate(`/runs/${run.run_id}`);
        return;
      }
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      form.append("session_id", session.session_id);
      const run = await uploadRun(tenantId, form);
      navigate(`/runs/${run.run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start run");
    } finally { setBusy(false); }
  }

  const scopeHint = mode === "crawl"
    ? "Optional — defaults to the crawl domain and its subdomains. Add a CDN / other host to widen it."
    : "Optional for uploads.";

  return (
    <form className="card nr-form" onSubmit={submit}>
      <h2 className="nr-title">New recon run</h2>
      <p className="muted nr-sub">Declaring who authorized this is the authorization acknowledgment.</p>

      <div className="nr-modes" role="radiogroup" aria-label="Run mode">
        <label className={mode === "upload" ? "is-active" : ""}>
          <input type="radio" name="mode" value="upload" checked={mode === "upload"}
            onChange={() => setMode("upload")} /> Upload a file
        </label>
        <label className={mode === "crawl" ? "is-active" : ""}>
          <input type="radio" name="mode" value="crawl" checked={mode === "crawl"}
            onChange={() => setMode("crawl")} /> Crawl a domain
        </label>
      </div>

      {mode === "upload" ? (
        <div className="nr-field"><label htmlFor="file">JavaScript file</label>
          <input id="file" type="file" accept=".js,.mjs,text/javascript"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
      ) : (
        <div className="nr-field"><label htmlFor="domain">Domain</label>
          <input id="domain" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.io" /></div>
      )}

      <div className="nr-field">
        <label htmlFor="scope">Scope hosts</label>
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
          <input id="scope" value={scopeInput} placeholder="example.com  (or *.example.com)"
            onChange={(e) => setScopeInput(e.target.value)} onKeyDown={onScopeKeyDown} />
          <button type="button" className="nr-add" onClick={addHost}
            disabled={scopeInput.trim() === ""}>Add</button>
        </div>
        <p className="muted nr-hint">{scopeHint}</p>
      </div>

      <div className="nr-field"><label htmlFor="auth">Authorized by</label>
        <input id="auth" value={authorizedBy} placeholder="your name / ticket"
          onChange={(e) => setAuthorizedBy(e.target.value)} /></div>

      {error && <p className="sev-high nr-error">{error}</p>}
      <button type="submit" className="btn-primary nr-submit" disabled={!ready || busy}>
        {busy ? "Starting…" : mode === "crawl" ? "Crawl domain" : "Analyze file"}
      </button>
    </form>
  );
}
