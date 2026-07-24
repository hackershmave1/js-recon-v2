import { useState } from "react";
import type React from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { createSession, uploadRun } from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";

export function NewRunPanel() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [scopeHost, setScopeHost] = useState("");
  const [authorizedBy, setAuthorizedBy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = scopeHost.trim() !== "" && authorizedBy.trim() !== "" && file !== null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId || !file) return;
    setBusy(true); setError(null);
    try {
      const session = await createSession(tenantId, {
        scope_hosts: [scopeHost.trim()], authorized_by: authorizedBy.trim(),
      });
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
      <div><label htmlFor="scope">Scope host</label>
        <input id="scope" value={scopeHost} onChange={(e) => setScopeHost(e.target.value)} placeholder="example.com" /></div>
      <div><label htmlFor="auth">Authorized by</label>
        <input id="auth" value={authorizedBy} onChange={(e) => setAuthorizedBy(e.target.value)} /></div>
      <div><label htmlFor="file">JavaScript file</label>
        <input id="file" type="file" accept=".js,.mjs,text/javascript"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>{busy ? "Starting…" : "Analyze"}</button>
    </form>
  );
}
