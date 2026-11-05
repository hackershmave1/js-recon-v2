// token-validity.js — reads the workspace session token's expiry so the popup's sign-in gate shows
// "sign in" up front instead of a signed-in UI whose first upload then 401s mid-capture.
//
// The token is `b64url(json payload).b64url(hmac)` (apps/platform/src/recon/auth/token.py). The
// client has no key, so this never checks the signature — the server stays authoritative. The local
// read only ever turns a KNOWN-expired token into "sign in"; anything it can't read is "unknown" and
// left to the server's verdict, so a format change or a skewed clock can't lock anyone out.
// Pure (no chrome/DOM) so a Node test covers it.

const SKEW_SECONDS = 60;

function decodeBase64Url(part) {
  const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
  return atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
}

// The payload's `exp` (epoch seconds), or null when the token is not in the shape we know.
export function tokenExpiry(token) {
  const parts = String(token || '').split('.');
  if (parts.length !== 2 || !parts[0]) return null;
  try {
    const payload = JSON.parse(decodeBase64Url(parts[0]));
    const exp = payload && !Array.isArray(payload) ? payload.exp : null;
    return Number.isInteger(exp) ? exp : null;
  } catch (e) {
    return null;
  }
}

// What getStatus tells the popup: a truthy marker (never the raw token) when signed in, '' when
// not, and whether a held token is known to have expired. Expired at `now >= exp`, as the server
// checks, minus a skew so the gate flips slightly early rather than one request too late.
export function authMarker(token, { nowSec = Math.floor(Date.now() / 1000), trustLocalExpiry = true } = {}) {
  if (!token) return { authToken: '', authExpired: false };
  const exp = trustLocalExpiry ? tokenExpiry(token) : null;
  if (exp !== null && nowSec >= exp - SKEW_SECONDS) return { authToken: '', authExpired: true };
  return { authToken: 'session', authExpired: false };
}
