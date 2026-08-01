import { useState } from "react";
import type React from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { createSession, startRun, uploadRun } from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";

export function NewRunPanel() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"upload" | "crawl">("upload");
  const [scopeHost, setScopeHost] = useState("");
  const [authorizedBy, setAuthorizedBy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targetReady = mode === "upload" ? file !== null : domain.trim() !== "";
  const ready = scopeHost.trim() !== "" && authorizedBy.trim() !== "" && targetReady;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId) return;
    setBusy(true); setError(null);
    try {
      // Attach the active engagement (picked in the Sessions engagement switcher) so a
      // new run's session rolls up under it. Absent -> body stays {scope_hosts, authorized_by}.
      const engagementId = localStorage.getItem("recon.engagementId");
      const session = await createSession(tenantId, {
        scope_hosts: [scopeHost.trim()], authorized_by: authorizedBy.trim(),
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

  return (
    <form className="card" onSubmit={submit}>
      <h2>New recon run</h2>
      <p className="muted">Declaring a scope host + who authorized this is the authorization acknowledgment.</p>
      <div role="radiogroup" aria-label="Run mode">
        <label><input type="radio" name="mode" value="upload" checked={mode === "upload"}
          onChange={() => setMode("upload")} /> Upload a file</label>
        <label><input type="radio" name="mode" value="crawl" checked={mode === "crawl"}
          onChange={() => setMode("crawl")} /> Crawl a domain</label>
      </div>
      <div><label htmlFor="scope">Scope host</label>
        <input id="scope" value={scopeHost} onChange={(e) => setScopeHost(e.target.value)} placeholder="example.com" /></div>
      <div><label htmlFor="auth">Authorized by</label>
        <input id="auth" value={authorizedBy} onChange={(e) => setAuthorizedBy(e.target.value)} /></div>
      {mode === "upload" ? (
        <div><label htmlFor="file">JavaScript file</label>
          <input id="file" type="file" accept=".js,.mjs,text/javascript"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
      ) : (
        <div><label htmlFor="domain">Domain</label>
          <input id="domain" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.io" /></div>
      )}
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>
        {busy ? "Starting…" : mode === "crawl" ? "Crawl" : "Analyze"}
      </button>
    </form>
  );
}
