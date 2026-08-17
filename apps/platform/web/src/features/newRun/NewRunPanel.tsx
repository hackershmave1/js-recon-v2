import { useState } from "react";
import type React from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { createSession, startRun, uploadRun } from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";
import "./newRun.css";

const UploadIcon = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 14.9A5 5 0 0 0 8 21h10a4 4 0 0 0 1-7.9" /><path d="M12 12v9" /><path d="m8 15 4-4 4 4" /><path d="M6.5 9a5.5 5.5 0 0 1 10.8-1.2" />
  </svg>
);
const GlobeIcon = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18Z" />
  </svg>
);

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// A bare host already covers its subdomains server-side, so `host` + `*.host` is
// redundant — keep the wider bare form, matching what the backend stores, so the
// chips shown equal the scope saved (no silent server-side normalization). A LONE
// wildcard is preserved as typed: collapsing it to the apex would WIDEN scope,
// which this tool must never do silently.
export function addScopeHost(hosts: string[], raw: string): string[] {
  const host = raw.trim();
  if (host === "" || hosts.includes(host)) return hosts;
  const bare = host.startsWith("*.") ? host.slice(2) : host;
  if (host === bare) return [...hosts.filter((h) => h !== `*.${bare}`), bare];
  return hosts.includes(bare) ? hosts : [...hosts, host];
}

export function NewRunPanel() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"upload" | "crawl">("upload");
  const [scopeHosts, setScopeHosts] = useState<string[]>([]);
  const [scopeInput, setScopeInput] = useState("");
  const [authorizedBy, setAuthorizedBy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [domain, setDomain] = useState("");
  const [capture, setCapture] = useState(false);
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
    setScopeHosts((prev) => addScopeHost(prev, host));
    setScopeInput("");
  }

  function onScopeKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") { e.preventDefault(); addHost(); }  // add, don't submit
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId) return;
    setBusy(true); setError(null);
    // Fold a typed-but-not-yet-added host into the scope so a value left in the
    // input isn't silently dropped on submit.
    const pending = scopeInput.trim();
    const hosts = pending ? addScopeHost(scopeHosts, pending) : scopeHosts;
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
        // Only send `capture` when opted in — the backend defaults it off and the
        // NewRunPanel tests assert the lean body for a normal crawl.
        const run = await startRun(tenantId, {
          session_id: session.session_id, target: domain.trim(),
          ...(capture ? { capture: true } : {}),
        });
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
      <p className="muted nr-sub">Point the analyzer at a JavaScript file or a live domain. Naming who authorized this is the authorization acknowledgment.</p>

      <div className="nr-modes" role="radiogroup" aria-label="Run mode">
        <label className={mode === "upload" ? "is-active" : ""}>
          <input type="radio" name="mode" value="upload" checked={mode === "upload"}
            onChange={() => setMode("upload")} />
          <UploadIcon /> Upload a file
        </label>
        <label className={mode === "crawl" ? "is-active" : ""}>
          <input type="radio" name="mode" value="crawl" checked={mode === "crawl"}
            onChange={() => setMode("crawl")} />
          <GlobeIcon /> Crawl a domain
        </label>
      </div>

      {mode === "upload" ? (
        <div className="nr-field">
          <label htmlFor="file">JavaScript file</label>
          <label
            className={"nr-drop" + (dragOver ? " over" : "") + (file ? " has-file" : "")}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}>
            <span className="nr-drop-icon" aria-hidden="true"><UploadIcon /></span>
            {file ? (
              <span className="nr-drop-file">
                <span className="nr-drop-name">{file.name}</span>
                <span className="nr-drop-size">{formatBytes(file.size)} · click to replace</span>
              </span>
            ) : (
              <span className="nr-drop-cta">
                <span className="nr-drop-lead">Drop a <code>.js</code> file, or <span className="nr-drop-browse">browse</span></span>
                <span className="nr-drop-sub">.js · .mjs</span>
              </span>
            )}
            <input id="file" type="file" accept=".js,.mjs,text/javascript" className="nr-file-native"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
        </div>
      ) : (
        <>
          <div className="nr-field"><label htmlFor="domain">Domain</label>
            <input id="domain" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.io" /></div>
          <label className="nr-capture">
            <input type="checkbox" checked={capture} onChange={(e) => setCapture(e.target.checked)} />
            <span className="nr-capture-text">
              <span className="nr-capture-title">Runtime capture <span className="nr-beta">beta</span></span>
              <span className="muted nr-capture-hint">Execute the page in a headless browser to capture runtime JS — workers, injected, and eval&apos;d code the static crawl can&apos;t see. Must be enabled server-side.</span>
            </span>
          </label>
        </>
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
