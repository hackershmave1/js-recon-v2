// ADR-0011: the extension's "Open in workspace" arrives as `<origin>/…#handoff=<code>`.
//
// This module is imported FIRST by main.tsx, so it evaluates before the router, React or any
// other app module can read `location`. At evaluation it (1) lifts the code out of the
// fragment into module memory and (2) rewrites the current history entry without it
// (`history.replaceState`), so the code is not left in the address bar or this entry. A
// fragment is never sent to a server or in `Referer`, but the strip still matters: a copied or
// bookmarked URL must not carry a (possibly still-live) credential. Safety does NOT rest on the
// erasure — the code is single-use and dies in 30 s (browser-global history is not verified).
//
// The exchange is started at most ONCE (a memoised promise): React StrictMode runs effects
// twice in dev, and a second POST would spend an already-used code.
import { exchangeHandoffCode, type LoginResult } from "../api/apiClient";

const FRAGMENT = /^#handoff=([A-Za-z0-9_-]{43})$/;

let capturedCode: string | null = null;
let exchange: Promise<LoginResult | null> | null = null;

/** Lift the code out of `location.hash` and clear it. Exported for tests; runs once at import. */
export function captureHandoffFragment(): void {
  try {
    const hash = window.location.hash;
    if (!hash.includes("handoff=")) return;
    const match = FRAGMENT.exec(hash);
    capturedCode = match ? match[1] : null;
    // Strip even a malformed handoff fragment: nothing credential-shaped stays in the bar.
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  } catch {
    capturedCode = null;
  }
}

/** True while a captured code has not yet been exchanged or given up on. */
export function hasHandoffCode(): boolean {
  return capturedCode !== null || exchange !== null;
}

/** Spend the code (once). Resolves to the login result, or null on ANY failure (no detail). */
export function startHandoff(): Promise<LoginResult | null> {
  if (exchange) return exchange;
  const code = capturedCode;
  capturedCode = null; // never kept after use
  exchange = code
    ? exchangeHandoffCode(code).catch(() => null)
    : Promise.resolve(null);
  return exchange;
}

/** Test seam: forget module state. */
export function resetHandoffForTests(): void {
  capturedCode = null;
  exchange = null;
}

captureHandoffFragment();
