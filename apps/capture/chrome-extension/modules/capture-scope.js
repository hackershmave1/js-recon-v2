// capture-scope.js — the capture's scope host and the new-site check (capture lifecycle).
// Pure (no chrome/DOM), so the worker imports it and a Node test covers it.
//
// The two lists MIRROR server lists; tests/test_capture_scope.mjs parses the Python files and fails
// if they drift:
// - SHARED_SUFFIXES = recon/fetch/egress.py _PUBLIC_SUFFIX_DENYLIST. "acme.io and all its
//   subdomains" must never become "every *.github.io site": the server drops such an entry from the
//   session scope, and the extension's gate would capture every site under it.
// - NOISE_HOSTS = the hosts of the built-in third-party rules (analytics, telemetry, ads, vendor CDNs),
//   generated from the platform's third_party_rules.json (modules/third-party-rules.js). Scripts from these
//   never raise the "new site seen" badge.

import { NOISE_HOSTS } from './third-party-rules.js';
import { teamHosts } from './team-rules.js';

export const SHARED_SUFFIXES = new Set([
  'co.uk', 'org.uk', 'gov.uk', 'ac.uk', 'co.jp', 'com.au', 'com.br', 'co.in', 'co.za', 'github.io',
  'gitlab.io', 'herokuapp.com', 'web.app', 'firebaseapp.com', 'pages.dev', 'workers.dev',
  'netlify.app', 'vercel.app', 'now.sh', 'cloudfront.net', 'amazonaws.com', 's3.amazonaws.com',
  'azurewebsites.net', 'blob.core.windows.net', 'appspot.com', 'run.app', 'blogspot.com',
  'wordpress.com', 'glitch.me', 'repl.co', 'surge.sh',
]);

export { NOISE_HOSTS };

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

function cleanHost(host) {
  return String(host || '').trim().toLowerCase().replace(/\.$/, '').replace(/^www\./, '');
}

// Whether a host can stand for itself plus its subdomains: false for an IP literal, an IPv6
// address, or a single-label host (localhost), whose scope is the exact host.
export function takesSubdomains(host) {
  const h = cleanHost(host);
  // An all-numeric last label is an IP literal or short form (the server's rule too, egress.py).
  const numericTail = /^\d+$/.test(h.split('.').pop() || '');
  return !!h && !IPV4.test(h) && !numericTail && !h.includes(':') && h.includes('.');
}

// The scope a tab's host stands for: its registrable domain ("app.acme.io" → "acme.io"), keeping
// one more label under a shared suffix ("foo.github.io" stays "foo.github.io", "app.acme.co.uk" →
// "acme.co.uk"). IPs and single-label hosts are returned as they are.
export function apexForHost(host) {
  const h = cleanHost(host);
  if (!takesSubdomains(h)) return h;
  const labels = h.split('.');
  let keep = 2;
  while (keep < labels.length && SHARED_SUFFIXES.has(labels.slice(-keep).join('.'))) keep += 1;
  return labels.slice(-keep).join('.');
}

export function isNoiseHost(host) {
  const h = cleanHost(host);
  if (!h) return false;
  for (const entry of NOISE_HOSTS) {
    if (h === entry || h.endsWith('.' + entry)) return true;
  }
  // The team's additions (workspace Settings): only ever ADD to what is dropped.
  for (const entry of teamHosts()) {
    if (h === entry || h.endsWith('.' + entry)) return true;
  }
  return false;
}

// Whether a host is inside a capture scope list (exact host, or a subdomain of an entry that takes
// subdomains). Mirrors hook-matches.hostInScope for the capture's own scope list.
export function inScope(host, scope) {
  const h = cleanHost(host);
  return (scope || []).some((entry) => {
    const e = cleanHost(entry);
    return h === e || (takesSubdomains(e) && h.endsWith('.' + e));
  });
}
