import { useEffect, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { mintPairingToken, ApiError } from "../../api/apiClient";
import type { PairingToken } from "../../api/types";
import { Icon } from "../../shell/icons";
import "./pairDevice.css";

// A shape-valid tenant is not necessarily a real row (tenants are bootstrapped
// out-of-band via `python -m recon.bootstrap`), and a fresh server has no
// RECON_PAIRING_KEY — so the mint has three distinct, reachable failures. Give each
// honest copy instead of a generic "failed" (design-gate must-fix).
function mintErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 503) return "Pairing isn't enabled on this server — set RECON_PAIRING_KEY to turn it on.";
    if (err.status === 404) {
      // The mint 404s with detail "unknown tenant" for a shape-valid id that has no row.
      // But /pairing is only mounted when capture ingest is on; with it off the route is
      // unmounted and 404s with a generic detail — don't misattribute that to a missing tenant.
      return /tenant/i.test(err.message)
        ? "This tenant isn't recognized by the server. Bootstrap it, or switch to a known tenant."
        : "Pairing isn't available on this server.";
    }
    return err.message;
  }
  // A network failure rejects with a raw TypeError (not an ApiError), so it lands here.
  return "Couldn't reach the server. Check that it's running, then try again.";
}

export function PairDeviceModal({ tenantId, onClose }: { tenantId: string; onClose: () => void }) {
  const [token, setToken] = useState<PairingToken | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0); // bump = re-mint (retry after an error)
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  // Mint on open + on each retry. Live-guard so a resolve that lands after the modal
  // closes (or StrictMode's dev double-invoke) can't setState on an unmounted node.
  useEffect(() => {
    let live = true;
    setToken(null); setError(null); setCopied(false); setCopyFailed(false);
    mintPairingToken(tenantId)
      .then((t) => { if (live) setToken(t); })
      .catch((e) => { if (live) setError(mintErrorMessage(e)); });
    return () => { live = false; };
  }, [tenantId, nonce]);

  async function copy() {
    // navigator.clipboard is undefined on a non-secure (plain-http) origin — degrade to
    // manual copy rather than throwing on a token the readonly field already exposes.
    const clip = navigator.clipboard;
    if (!token || !clip?.writeText) { setCopyFailed(true); return; }
    try {
      await clip.writeText(token.token);
      setCopied(true); setCopyFailed(false);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopyFailed(true);
    }
  }

  const hours = token ? Math.max(1, Math.round(token.ttlSeconds / 3600)) : 0;

  return (
    <div className="pair-scrim" onClick={onClose}>
      <div className="pair-modal" onClick={(e) => e.stopPropagation()}
        role="dialog" aria-modal="true" aria-label="Pair a capture device">
        <div className="pair-head">
          <span className="pair-head-icon"><Icon name="link" size={18} /></span>
          <div>
            <h2>Pair a capture device</h2>
            <div className="pair-head-sub">Route your browser-extension captures into this tenant.</div>
          </div>
          <button type="button" className="pair-head-x" onClick={onClose} aria-label="Close">
            <Icon name="x" size={18} />
          </button>
        </div>

        <div className="pair-body">
          {!token && !error && <div className="pair-msg loading">Generating a pairing code…</div>}
          {error && <div className="pair-msg error" role="alert">{error}</div>}
          {token && (
            <>
              <label className="pair-label" htmlFor="pair-token">PAIRING CODE</label>
              <div className="pair-token-row">
                <input id="pair-token" className="pair-token" readOnly value={token.token}
                  onFocus={(e) => e.currentTarget.select()} />
                <button type="button" className={`pair-copy${copied ? " done" : ""}`} onClick={copy}>
                  {copied ? "Copied ✓" : "Copy"}
                </button>
              </div>
              <div className="pair-expiry">expires in ~{hours}h · paste once, no re-entry needed</div>
              {copyFailed && (
                <div className="pair-hint">Couldn't reach the clipboard — select the code above and copy it manually.</div>
              )}
              <div className="pair-steps">
                Open the extension → <b>Settings</b> → <b>Pairing token</b>, paste this code, and capture as
                usual. Captures from your logged-in browser land in <b>this tenant</b> until the code expires.
              </div>
            </>
          )}
        </div>

        <div className="pair-foot">
          {error && (
            <button type="button" className="pair-btn primary" onClick={() => setNonce((n) => n + 1)}>
              Try again
            </button>
          )}
          <button type="button" className="pair-btn" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}

// Tenant-level top-bar action. Rendered inside TenantGate, so tenantId is non-null in
// practice — but narrow it for tsc-strict and bail defensively if somehow absent.
export function PairDeviceButton() {
  const { tenantId } = useTenant();
  const [open, setOpen] = useState(false);
  if (!tenantId) return null;
  return (
    <>
      <button type="button" className="shell-btn" onClick={() => setOpen(true)}>
        <Icon name="link" size={15} />
        Pair device
      </button>
      {open && <PairDeviceModal tenantId={tenantId} onClose={() => setOpen(false)} />}
    </>
  );
}
