import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { getAssets } from "../../api/apiClient";
import { TERMINAL_STATES, type AssetsManifest } from "../../api/types";
import "./discovery.css";

// A short headline per failure category. The detailed, SAFE copy is the backend
// `reason` (recon.runs.failure) — including the capture-extension pointer for
// access_denied / rate_limited — so the wording lives in exactly one place.
function failureTitle(category: string, host: string | null): string {
  switch (category) {
    case "out_of_scope":
      return host ? `The crawl reached ${host}, outside your scope` : "The crawl left the engagement scope";
    case "access_denied":
      return "The target refused the crawler";
    case "rate_limited":
      return "The target rate-limited the crawler";
    case "dns_error":
      return "The target host could not be resolved";
    case "blocked_address":
      return "Blocked by the egress guard";
    case "invalid_target":
      return "The target URL is invalid";
    case "timeout":
      return "The target timed out";
    case "server_error":
    case "http_error":
      return "The target returned an error";
    default:
      return "The recon run failed";
  }
}

// The terminal run-outcome explainer, in two shapes:
//  (a) a FAILED run with a classified reason (recon.runs.failure) — shows WHY it
//      failed with curated, safe copy + a category-appropriate headline. This is
//      the case a bot-blocked / geo-redirected / out-of-scope crawl lands in, and
//      it replaces the old one-size-fits-all "another origin / CDN" guidance.
//  (b) a finished crawl that discovered zero in-scope JS (the "done but empty"
//      case) — the manifest has a non-null `domain`; uploads never hit this.
export function DiscoveryEmpty({
  runId,
  state,
  failureCategory,
  failureReason,
  failureHost,
}: {
  runId: string;
  state: string | null;
  failureCategory: string | null;
  failureReason: string | null;
  failureHost: string | null;
}) {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [manifest, setManifest] = useState<AssetsManifest | null>(null);
  const terminal = state != null && TERMINAL_STATES.has(state);
  const failed = terminal && failureCategory != null && failureReason != null;

  useEffect(() => {
    // Only shape (b) needs the manifest's domain; a classified failure (a) has its
    // own message, so skip the fetch there.
    if (!tenantId || !terminal || failed) return;
    let cancelled = false;
    getAssets(tenantId, runId)
      .then((m) => {
        if (!cancelled) setManifest(m);
      })
      .catch(() => {
        /* non-fatal — the manifest is best-effort */
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId, runId, terminal, failed]);

  if (!terminal) return null;

  // (a) classified failure — failureReason is safe, curated backend copy.
  if (failed) {
    return (
      <div className="card de">
        <div className="de-title">{failureTitle(failureCategory, failureHost)}</div>
        <p className="muted de-body">{failureReason}</p>
        <button type="button" className="btn-primary de-cta" onClick={() => navigate("/")}>
          Start a new run
        </button>
      </div>
    );
  }

  // (b) empty crawl — finished with zero in-scope assets.
  if (!manifest || manifest.domain == null || manifest.assets.length > 0) return null;
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
