// hook-matches.js — the match patterns for the opt-in main-world response-body hook (D45b2).
//
// The hook wraps window.fetch / XMLHttpRequest in the PAGE's own world, so it must only run
// where capture would: a hook registered on <all_urls> patched every site the operator visited
// (out of scope included), was visible to bot detection that checks fetch is native, and had
// Chrome blame page-own CSP violations (a Cloudflare challenge's `fetch('data:,')` probe) on the
// extension. These patterns mirror background.isInScope: exact host, plus subdomains unless
// includeSubdomains is false; no scope = nothing (fail-closed) unless "capture every tab".
// Pure (no chrome/DOM) so a Node test covers it.
import { normalizeRootDomains } from './normalize-scope.js';

// A Chrome match-pattern host: DNS labels or an IPv4 address (no port, no brackets/spaces) —
// anything else makes registerContentScripts throw and the whole registration fail.
const PATTERN_HOST = /^[a-z0-9-]+(\.[a-z0-9-]+)*$/;

export function hookMatchPatterns(settings) {
  const s = settings || {};
  if (s.captureResponseBodies !== true) return [];
  const hosts = s.useDomainScope ? normalizeRootDomains(s.domainScopes) : [];
  if (hosts.length === 0) return s.captureEverything === true ? ['<all_urls>'] : [];
  const patterns = [];
  for (const host of hosts) {
    if (!PATTERN_HOST.test(host)) continue;
    patterns.push(`*://${host}/*`);
    if (s.includeSubdomains !== false) patterns.push(`*://*.${host}/*`);
  }
  return patterns;
}
