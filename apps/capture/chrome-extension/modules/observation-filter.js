// observation-filter.js — pure helpers for runtime API-call observations (DEBT D45b1). An
// "observation" is a { method, url } the app actually issued (an XHR/fetch). Feeding these to
// the platform promotes a statically-SUSPECTED endpoint to a CONFIRMED one (the correlate
// stage matches observed URLs to endpoint findings). These helpers decide which observed calls
// are worth recording and normalize the URL to the shape the platform matcher expects
// (scheme://host/path, query dropped). Dependency-free so it stays importable in the Node tests,
// mirroring asset-classifier.js / inline-relevance.js.

// Response content-types that are NOT API surface even when fetched via XHR/fetch (a script that
// XHRs an image/font/media blob or a stylesheet). Everything else — json/graphql/text/form/
// octet-stream — is treated as API surface (kept).
const NON_API_CONTENT_TYPES = [
  /^image\//i, /^font\//i, /^audio\//i, /^video\//i, /^text\/css\b/i, /^application\/font/i,
];

// Same-origin telemetry/beacon paths the host denylist can't catch (e.g. app.target.com/collect).
// Kept NARROW/high-confidence so a real app endpoint (/metrics, /track as a feature) is not
// false-dropped — the well-known analytics HOSTS are already handled by the denylist upstream.
const TELEMETRY_PATH = /(^|\/)(collect|beacon|rum|telemetry|sentry-tunnel|gtag|gtm)(\/|$)/i;

// Normalize an observed URL to scheme://host/path (query + fragment dropped) — the exact shape
// the platform's correlate matcher aligns on. Returns null for a non-http(s) or unparseable URL.
export function normalizeObservedUrl(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return `${u.protocol}//${u.host}${u.pathname}`;
  } catch (e) {
    return null;
  }
}

// True if a response content-type looks like API surface (or is absent — an XHR/fetch with no
// content-type is still a programmatic call worth recording).
export function isApiIshObservation(contentType) {
  if (!contentType) return true;
  const ct = String(contentType).toLowerCase();
  for (const re of NON_API_CONTENT_TYPES) {
    if (re.test(ct)) return false;
  }
  return true;
}

// True if the URL's PATH is a same-origin telemetry/beacon (drop it — it's noise, not app API).
export function isTelemetryPath(url) {
  try {
    return TELEMETRY_PATH.test(new URL(url).pathname);
  } catch (e) {
    return false;
  }
}
