// asset-classifier.js — lightweight, dependency-free heuristics that drive the
// redesigned popup: asset classification (app/lib/cms/tracker), third-party
// detection, third-party file matching, and a bounded secret COUNTER.
//
// Design note: this is intentionally cheap (regex + string ops). The secret
// counter returns a count only — never the matched values — so secrets are never
// persisted or surfaced through the popup status/file APIs.

import { FILE_PATTERNS } from './third-party-rules.js';
import { teamFilePatterns } from './team-rules.js';

const TRACKER_HOSTS = [
  'google-analytics.com', 'googletagmanager.com', 'doubleclick.net', 'segment.io',
  'segment.com', 'mixpanel.com', 'hotjar.com', 'facebook.net', 'fbcdn.net',
  'amplitude.com', 'sentry.io', 'clarity.ms', 'newrelic.com', 'optimizely.com'
];

const LIB_HINTS = [
  /jquery[.-]/i, /\breact(-dom)?[.-]/i, /\bvue[.-]/i, /angular[.-]/i, /lodash/i,
  /bootstrap/i, /\bd3[.-]/i, /moment/i, /polyfill/i, /\bvendors?[~.\-]/i, /runtime~/i
];

// The built-in third-party FILE patterns (ADR-0010, generated from the platform's
// third_party_rules.json). Host entries are not patterns here: the admission rule
// (modules/script-admission.js) treats a known third-party HOST separately so it never drops a host the
// researcher typed into the scope; a file pattern applies on any host.
export const DEFAULT_PROFILE_PATTERNS = [
  ...FILE_PATTERNS,
  // Kept as before: these three analytics hosts were always dropped (also under "Capture every tab",
  // which skips the admission rule), so that behaviour is unchanged.
  '*.google-analytics.com', '*.googletagmanager.com', '*.doubleclick.net',
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

// `*` wildcard match, case-insensitive, in O(pattern x text) with no RegExp: a team-supplied pattern
// (modules/team-rules.js) is untrusted input and a regex built from `a*a*a*a` backtracks polynomially
// on a long hostile path. Same algorithm as the platform's glob_match (third_party_rules.py).
const MAX_MATCHED_LENGTH = 2048;
export function globMatch(pattern, text) {
  const p = String(pattern).toLowerCase();
  const s = String(text).toLowerCase().slice(0, MAX_MATCHED_LENGTH);
  let pi = 0; let si = 0; let star = -1; let mark = -1;
  while (si < s.length) {
    if (pi < p.length && p[pi] === '*') { star = pi; mark = si; pi += 1; }
    else if (pi < p.length && p[pi] === s[si]) { pi += 1; si += 1; }
    else if (star !== -1) { mark += 1; pi = star + 1; si = mark; }
    else return false;
  }
  while (pi < p.length && p[pi] === '*') pi += 1;
  return pi === p.length;
}

// A bare-domain pattern (e.g. "*.doubleclick.net") matches the host; a path-ish pattern (starts with
// `/`) matches the pathname.
export function matchesPattern(url, pattern) {
  if (!pattern) return false;
  const pat = pattern.trim();
  if (!pat) return false;
  const host = hostnameOf(url);
  const path = pathOf(url);
  if (pat.startsWith('/')) return globMatch(pat, path);
  if (pat.includes('/')) return globMatch(pat, host + path) || globMatch(pat, path);
  return globMatch(pat, host);
}

// Whether `url` is a known third-party FILE: a built-in pattern or one of the team's additions
// (modules/team-rules.js, edited in workspace Settings). No setting turns this off.
export function matchesThirdPartyFile(url) {
  return [...DEFAULT_PROFILE_PATTERNS, ...teamFilePatterns()].some((p) => matchesPattern(url, p));
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
