// hook-matches.js — the ONE capture-scope host rule, and the match patterns for the opt-in
// main-world response-body hook (D45b2) derived from it.
//
// The hook wraps window.fetch / XMLHttpRequest in the PAGE's own world, so it must only run
// where capture would: a hook registered on <all_urls> patched every site the operator visited
// (out of scope included), was visible to bot detection that checks fetch is native, and had
// Chrome blame page-own CSP violations (a Cloudflare challenge's `fetch('data:,')` probe) on the
// extension. Both background.isInScope and the hook's patterns read `scopeRule`, so they cannot
// disagree (a stored pre-D40 entry like `*.acme.io` matches nothing for BOTH, not just one).
// Pure (no chrome/DOM) so a Node test covers it.

// No explicit scope -> fail CLOSED (nothing). "Capture every tab" (Settings, behind a confirm)
// OVERRIDES the scope (capture lifecycle decision, 2026-11-08): every capture now starts with a
// scope, so honouring it only when none was set would make it silently dead.
// Entries are used as stored (trimmed + lowercased), exactly as the capture gate always has.
export function scopeRule(settings) {
  const s = settings || {};
  const list = Array.isArray(s.domainScopes) ? s.domainScopes : [];
  if (s.captureEverything === true) return { everything: true, hosts: [], subdomains: false };
  if (!s.useDomainScope || list.length === 0) {
    return { everything: false, hosts: [], subdomains: false };
  }
  const hosts = list.map((e) => String(e || '').trim().toLowerCase()).filter(Boolean);
  return { everything: false, hosts, subdomains: s.includeSubdomains !== false };
}

export function hostInScope(settings, hostname) {
  const rule = scopeRule(settings);
  if (rule.everything) return true;
  const host = String(hostname || '').toLowerCase();
  return rule.hosts.some((entry) => host === entry || (rule.subdomains && host.endsWith('.' + entry)));
}

// A Chrome match-pattern host: DNS-style labels (underscore allowed — `dev_api.acme.io` is
// captured, so it must be hooked) or an IPv4 address; anything else (spaces, brackets, a port,
// a scheme) makes registerContentScripts throw and the WHOLE registration fail, so it is skipped
// — and such an entry matches no real hostname in hostInScope either.
const PATTERN_HOST = /^[a-z0-9_-]+(\.[a-z0-9_-]+)*$/;
const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

export function hookMatchPatterns(settings) {
  const s = settings || {};
  if (s.captureResponseBodies !== true) return [];
  const rule = scopeRule(s);
  if (rule.everything) return ['<all_urls>'];
  const patterns = [];
  for (const host of rule.hosts) {
    if (!PATTERN_HOST.test(host)) continue;
    patterns.push(`*://${host}/*`);
    // An IP address has no subdomains (Chrome would never match `*.10.0.0.1`).
    if (rule.subdomains && !IPV4.test(host)) patterns.push(`*://*.${host}/*`);
  }
  return patterns;
}
