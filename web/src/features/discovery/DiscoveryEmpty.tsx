import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { getAssets } from "../../api/apiClient";
import { TERMINAL_STATES, type AssetsManifest } from "../../api/types";
import "./discovery.css";

// The 0-asset empty-state (S4): a finished CRAWL that discovered zero in-scope JS
// — the proof.com case that motivated the scope-safety slice (a run that finished
// `done` with no data and no explanation). A crawl manifest has a non-null
// `domain`; an upload run has none, so this never fires for uploads. The manifest
// is fetched lazily, only once the run is terminal.
export function DiscoveryEmpty({ runId, state }: { runId: string; state: string | null }) {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [manifest, setManifest] = useState<AssetsManifest | null>(null);
  const terminal = state != null && TERMINAL_STATES.has(state);

  useEffect(() => {
    if (!tenantId || !terminal) return;
    let cancelled = false;
    getAssets(tenantId, runId).then((m) => { if (!cancelled) setManifest(m); }).catch(() => { /* non-fatal */ });
    return () => { cancelled = true; };
  }, [tenantId, runId, terminal]);

  if (!terminal || !manifest || manifest.domain == null || manifest.assets.length > 0) return null;

  return (
    <div className="card de">
      <div className="de-title">
        No in-scope JavaScript discovered on <span className="de-host">{manifest.domain}</span>
      </div>
      <p className="muted de-body">
        The crawl only follows scripts served from hosts in your scope. If this app loads its
        JavaScript from another origin — a CDN, a <code>www.</code> / <code>assets.</code> subdomain,
        or a third-party host — those files were skipped. Start a new run with those hosts in scope,
        or upload the bundle directly.
      </p>
      <button type="button" className="btn-primary de-cta" onClick={() => navigate("/")}>
        Start a new run
      </button>
    </div>
  );
}
