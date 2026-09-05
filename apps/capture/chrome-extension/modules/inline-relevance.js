// inline-relevance.js — CONTENT-based filter for captured inline <script> bodies (DEBT D45a).
//
// Why content-based, not URL-based: an inline analytics bootstrap (GTM/GA/Segment) lives ON an
// in-scope app page, so its synthetic capture URL IS the in-scope page — it sails straight
// through the URL-keyed scope + denylist gate (isInScope / shouldSkipUrl). The only thing that
// tells a GTM snippet or a Next.js hydration-data push apart from real app code is the CONTENT.
// This is the no-noise linchpin the D45 design review (Claim 3) flagged as REQUIRED.
//
// Dependency-free (regex + string ops), mirroring asset-classifier.js so it stays importable in
// the Node test suites.

// Serialized framework state / hydration payloads — DATA, not source (no logic/endpoints worth
// analyzing). These are the dominant inline-flood source on SPA route changes.
const SERIALIZED_DATA_SIGNATURES = [
  /self\.__next_f\b/,               // Next.js RSC streaming push
  /__NEXT_DATA__/,                  // Next.js page data
  /window\.__NUXT__\s*=/,           // Nuxt
  /__remixContext\b/,               // Remix
  /window\.__APOLLO_STATE__/,       // Apollo cache dump
  /\b__sveltekit_/,                 // SvelteKit
  /window\.__INITIAL_STATE__\s*=/,  // generic SSR state dump
];

// Third-party analytics / tag-manager / RUM bootstraps. Kept PRECISE (call/config shapes, not
// bare vendor words) so a real app endpoint like "/api/analytics" is never mistaken for noise.
const ANALYTICS_SIGNATURES = [
  /\bdataLayer\s*=\s*\[/, /\bdataLayer\.push\s*\(/, /\bgtag\s*\(/, /GTM-[A-Z0-9]{4,}/,
  /googletagmanager\.com/i, /google-analytics\.com/i, /\bga\s*\(\s*['"]/,
  /\banalytics\.(load|track|identify|page)\s*\(/, /\bmixpanel\.(init|track)\b/,
  /\bamplitude\.(getInstance|init)\b/, /\bfbq\s*\(/, /\b_hjSettings\b/, /\bhj\s*\(\s*['"]/,
  /\bclarity\s*\(\s*['"]/, /\bSentry\.(init|onLoad)\b/, /\bnewrelic\b/i,
];

// Minimum meaningful size — a lone `var x=1` or a bootstrap flag carries no recon value. Small
// enough to KEEP a real inline config object (endpoints live in those).
const MIN_INLINE_CHARS = 24;

// True if an inline <script> body is worth capturing (real app JS), false if it's noise
// (trivial, hydration data, or a third-party analytics bootstrap).
export function isRelevantInlineScript(content) {
  if (typeof content !== 'string') return false;
  const trimmed = content.trim();
  if (trimmed.length < MIN_INLINE_CHARS) return false;
  for (const re of SERIALIZED_DATA_SIGNATURES) {
    if (re.test(trimmed)) return false;
  }
  for (const re of ANALYTICS_SIGNATURES) {
    if (re.test(trimmed)) return false;
  }
  // Must look like executable JS (a statement/expression token), not a stray text blob.
  if (!/[;={}(]/.test(trimmed)) return false;
  return true;
}
