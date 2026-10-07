import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { getThreatModel, triggerThreatModel } from "../../api/apiClient";
import { useTenant } from "../../tenant/TenantContext";
import type { ThreatModelResponse, ThreatEntry } from "../../api/types";
import "./threat-model.css";

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];
const SEVERITY_LABEL: Record<string, string> = {
  critical: "CRITICAL", high: "HIGH", medium: "MEDIUM", low: "LOW", info: "INFO",
};

function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`tm-severity tm-sev-${severity}`}>{SEVERITY_LABEL[severity] ?? severity.toUpperCase()}</span>;
}

function TestStepList({ steps }: { steps: ThreatEntry["test_steps"] }) {
  if (!steps.length) return null;
  return (
    <ol className="tm-steps">
      {steps.map((s, i) => (
        <li key={i} className="tm-step">
          <span className="tm-step-action">{s.action}</span>
          <code className="tm-step-cmd">{s.command}</code>
          <div className="tm-step-expect">
            <span className="tm-vuln">Vulnerable: {s.expected_if_vulnerable}</span>
            <span className="tm-secure">Secure: {s.expected_if_secure}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}

function ThreatCard({ threat }: { threat: ThreatEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="tm-card">
      <button
        type="button"
        className="tm-card-header"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <SeverityBadge severity={threat.severity} />
        <span className="tm-card-title">{threat.title}</span>
        <span className="tm-owasp">{threat.owasp_category}</span>
        <span className="tm-chevron">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="tm-card-body">
          <p className="tm-description">{threat.description}</p>
          {threat.affected_endpoints.length > 0 && (
            <div className="tm-affected">
              <span className="tm-label">Affected endpoints</span>
              <ul className="tm-ep-list">
                {threat.affected_endpoints.map((ep, i) => <li key={i}><code>{ep}</code></li>)}
              </ul>
            </div>
          )}
          {threat.test_steps.length > 0 && (
            <div className="tm-steps-section">
              <span className="tm-label">Test steps</span>
              <TestStepList steps={threat.test_steps} />
            </div>
          )}
          {threat.citations.length > 0 && (
            <div className="tm-citations">
              <span className="tm-label">Citations</span>
              <ul className="tm-cite-list">
                {threat.citations.map((h) => <li key={h}><code className="tm-hash">{h.slice(0, 12)}…</code></li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ThreatModelPage({ sessionId }: { sessionId: string }) {
  const { tenantId } = useTenant();
  const [data, setData] = useState<ThreatModelResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchState = async () => {
    if (!tenantId) return;
    try {
      const result = await getThreatModel(tenantId, sessionId);
      setData(result);
      setFetchError(null);
      if (result.status === "done" || result.status === "failed") {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    } catch (err: any) {
      if (err?.status === 404) {
        setData(null);
      } else {
        setFetchError(err?.message ?? "Failed to load threat model");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchState();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [sessionId, tenantId]);

  useEffect(() => {
    if (data?.status === "pending" || data?.status === "running") {
      if (!pollRef.current) {
        pollRef.current = setInterval(fetchState, 3000);
      }
    } else {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    }
  }, [data?.status]);

  const handleGenerate = async () => {
    if (!tenantId) return;
    setTriggering(true);
    try {
      const result = await triggerThreatModel(tenantId, sessionId);
      setData(result);
      setFetchError(null);
    } catch (err: any) {
      setFetchError(err?.message ?? "Failed to start threat model generation");
    } finally {
      setTriggering(false);
    }
  };

  if (loading) return <div className="card"><p className="muted">Loading…</p></div>;

  const threats = data?.threats ?? [];
  const bySeverity = [...threats].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)
  );

  return (
    <div className="card tm-root">
      <div className="tm-header">
        <h2 className="rp-title">Threat Model</h2>
        {(!data || data.status === "done" || data.status === "failed") && (
          <button
            type="button"
            className="btn-primary tm-trigger"
            onClick={handleGenerate}
            disabled={triggering || data?.status === "pending" || data?.status === "running"}
          >
            {triggering ? "Starting…" : data?.status === "done" ? "Regenerate" : "Generate Threat Model"}
          </button>
        )}
      </div>

      {fetchError && <p className="tm-error">{fetchError}</p>}

      {!data && !fetchError && (
        <div className="tm-empty">
          <p>No threat model generated yet.</p>
          <p className="muted">
            Configure your LLM provider in the extension settings, then click
            <strong> Generate Threat Model</strong> to analyse this session's recon surface.
          </p>
        </div>
      )}

      {data?.status === "pending" && (
        <div className="tm-status">
          <span className="tm-spinner" aria-hidden="true" />
          <span>Queued — waiting to start…</span>
        </div>
      )}
      {data?.status === "running" && (
        <div className="tm-status">
          <span className="tm-spinner" aria-hidden="true" />
          <span>Analysing recon surface with {data.model ?? "LLM"}…</span>
        </div>
      )}
      {data?.status === "failed" && (
        <p className="tm-error">
          Generation failed: {data.error ?? "unknown error"}
          {data.error?.startsWith("no LLM API key") && (
            <> <Link to="/settings">Set a team key in Settings →</Link></>
          )}
        </p>
      )}

      {data?.status === "done" && (
        <>
          {data.analysis_summary && (
            <p className="tm-summary">{data.analysis_summary}</p>
          )}
          <div className="tm-meta">
            <span>{bySeverity.length} threat{bySeverity.length !== 1 ? "s" : ""}</span>
            {data.model && <span>· {data.provider}/{data.model}</span>}
            {data.prompt_tokens != null && (
              <span>· {(data.prompt_tokens + (data.completion_tokens ?? 0)).toLocaleString()} tokens</span>
            )}
            {data.generated_at && (
              <span>· {new Date(data.generated_at).toLocaleString()}</span>
            )}
          </div>

          {bySeverity.length === 0 ? (
            <p className="muted">No threats identified for this recon surface.</p>
          ) : (
            <div className="tm-threat-list">
              {bySeverity.map((t) => <ThreatCard key={t.id} threat={t} />)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
