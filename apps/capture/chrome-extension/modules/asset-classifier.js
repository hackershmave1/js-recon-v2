// asset-classifier.js — lightweight, dependency-free heuristics that drive the
// redesigned popup: asset classification (app/lib/cms/tracker), third-party
// detection, noise denylist matching, and a bounded secret COUNTER.
//
// Design note: this is intentionally cheap (regex + string ops). The secret
// counter returns a count only — never the matched values — so secrets are never
// persisted or surfaced through the popup status/file APIs.

const TRACKER_HOSTS = [
  'google-analytics.com', 'googletagmanager.com', 'doubleclick.net', 'segment.io',
  'segment.com', 'mixpanel.com', 'hotjar.com', 'facebook.net', 'fbcdn.net',
  'amplitude.com', 'sentry.io', 'clarity.ms', 'newrelic.com', 'optimizely.com'
];

const LIB_HINTS = [
  /jquery[.-]/i, /\breact(-dom)?[.-]/i, /\bvue[.-]/i, /angular[.-]/i, /lodash/i,
  /bootstrap/i, /\bd3[.-]/i, /moment/i, /polyfill/i, /\bvendors?[~.\-]/i, /runtime~/i
];

// Built-in "default profile" denylist patterns (WordPress, analytics, ad & CDN noise).
export const DEFAULT_PROFILE_PATTERNS = [
  '/wp-content/plugins/*', '/wp-includes/*', '*.google-analytics.com',
  '*.googletagmanager.com', '*.doubleclick.net', '*/gtag/js*', '*/jquery*.min.js'
];

function hostnameOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch (e) { return ''; }
}

function pathOf(url) {
  try { return (new URL(url).pathname || '').toLowerCase(); } catch (e) { return (url || '').toLowerCase(); }
}

export function classifyAsset(url) {
  const host = hostnameOf(url);
  const path = pathOf(url);
  const last = path.split('/').pop() || '';

  if (TRACKER_HOSTS.some((h) => host === h || host.endsWith('.' + h)) ||
      /\b(gtag|analytics|gtm|fbevents|hotjar)\b/i.test(last)) {
    return 'tracker';
  }
  if (path.includes('/wp-content/') || path.includes('/wp-includes/') || /\bwp-/.test(path)) {
    return 'cms';
  }
  if (LIB_HINTS.some((re) => re.test(last) || re.test(path)) || /\.min\.js(\?|$)/i.test(path)) {
    return 'lib';
  }
  return 'app';
}

// Same-site check: asset host equals the page host or is a subdomain of its
// registrable-ish domain (last two labels). Best-effort, no PSL.
export function isThirdParty(assetUrl, pageUrl) {
  const a = hostnameOf(assetUrl);
  const p = hostnameOf(pageUrl);
  if (!a || !p) return false;
  if (a === p) return false;
  const reg = (h) => h.split('.').slice(-2).join('.');
  return reg(a) !== reg(p);
}

// Glob-ish matcher: `*` is wildcard. A bare-domain pattern (e.g. "*.doubleclick.net")
// matches the host; a path-ish pattern (starts with `/`) matches the pathname.
export function matchesPattern(url, pattern) {
  if (!pattern) return false;
  const rx = new RegExp('^' + pattern.trim()
    .replace(/\*+/g, '*')                 // collapse runs of * (avoid catastrophic backtracking)
    .replace(/[.+?^${}()|[\]\\]/g, '\\$&')
    .replace(/\*/g, '.*') + '$', 'i');
  const host = hostnameOf(url);
  const path = pathOf(url);
  if (pattern.startsWith('/')) return rx.test(path);
  if (pattern.includes('/')) return rx.test(host + path) || rx.test(path);
  return rx.test(host);
}

export function matchesDenylist(url, rules = [], includeDefaultProfile = true) {
  const patterns = [
    ...(includeDefaultProfile ? DEFAULT_PROFILE_PATTERNS : []),
    ...rules.map((r) => (typeof r === 'string' ? r : r.pattern)).filter(Boolean)
  ];
  return patterns.some((p) => matchesPattern(url, p));
}

const SECRET_PATTERNS = [
  /(api[_-]?key|apikey)\s*[:=]\s*['"`]([^'"`]+)['"`]/gi,
  /(secret|password|pwd)\s*[:=]\s*['"`]([^'"`]+)['"`]/gi,
  /(token|bearer)\s*[:=]\s*['"`]([^'"`]+)['"`]/gi,
  /sk_live_[a-zA-Z0-9]{24,}/g,
  /pk_live_[a-zA-Z0-9]{24,}/g,
  /AKIA[0-9A-Z]{16}/g,
  /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g
];

// Returns a deduplicated COUNT of likely secrets. Never returns the values.
export function countSecrets(content) {
  if (typeof content !== 'string' || content.length === 0) return 0;
  const seen = new Set();
  for (const pattern of SECRET_PATTERNS) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(content)) !== null) {
      seen.add(match[0]);
      if (seen.size >= 999) return seen.size; // bound work on pathological inputs
    }
  }
  return seen.size;
}
