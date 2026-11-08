// capture-scope.js — the capture's scope host and the new-site check (capture lifecycle).
// Pure (no chrome/DOM), so the worker imports it and a Node test covers it.
//
// The two lists MIRROR server lists; tests/test_capture_scope.mjs parses the Python files and fails
// if they drift:
// - SHARED_SUFFIXES = recon/fetch/egress.py _PUBLIC_SUFFIX_DENYLIST. "acme.io and all its
//   subdomains" must never become "every *.github.io site": the server drops such an entry from the
//   session scope, and the extension's gate would capture every site under it.
// - NOISE_HOSTS = recon/findings/noise_hosts.py DEFAULT_NOISE_HOSTS (analytics, telemetry, ads, vendor
//   CDNs). Scripts from these never raise the "new site seen" badge.

export const SHARED_SUFFIXES = new Set([
  'co.uk', 'org.uk', 'gov.uk', 'ac.uk', 'co.jp', 'com.au', 'com.br', 'co.in', 'co.za', 'github.io',
  'gitlab.io', 'herokuapp.com', 'web.app', 'firebaseapp.com', 'pages.dev', 'workers.dev',
  'netlify.app', 'vercel.app', 'now.sh', 'cloudfront.net', 'amazonaws.com', 's3.amazonaws.com',
  'azurewebsites.net', 'blob.core.windows.net', 'appspot.com', 'run.app', 'blogspot.com',
  'wordpress.com', 'glitch.me', 'repl.co', 'surge.sh',
]);

export const NOISE_HOSTS = new Set([
  'addthis.com', 'adservice.google.com', 'ajax.aspnetcdn.com', 'ajax.googleapis.com',
  'amplitude.com', 'appboycdn.com', 'apple.com', 'apple.news', 'bootstrapcdn.com', 'branch.io',
  'braze.com', 'bugsnag.com', 'cdnjs.cloudflare.com', 'cloudflareinsights.com', 'code.jquery.com',
  'connect.facebook.net', 'datadoghq-browser-agent.com', 'datadoghq.com', 'doubleclick.net',
  'drift.com', 'dublincore.org', 'ecma-international.org', 'facebook.com', 'facebook.net',
  'fbcdn.net', 'fontawesome.com', 'fullstory.com', 'google-analytics.com', 'google.com',
  'googleadservices.com', 'googleapis.com', 'googlesyndication.com', 'googletagmanager.com',
  'gstatic.com', 'gvt1.com', 'gvt2.com', 'heapanalytics.com', 'hotjar.com', 'ietf.org',
  'ingest.sentry.io', 'intercom.io', 'intercomassets.com', 'intercomcdn.com', 'js.stripe.com',
  'jsdelivr.net', 'json-ld.org', 'launchdarkly.com', 'logrocket.com', 'logrocket.io',
  'mixpanel.com', 'mouseflow.com', 'mozilla.org', 'mxpnl.com', 'newrelic.com', 'nr-data.net',
  'openapis.org', 'optimizely.com', 'purl.org', 'quantserve.com', 'recaptcha.net',
  'rfc-editor.org', 'schema.org', 'scorecardresearch.com', 'segment.com', 'segment.io',
  'sentry-cdn.com', 'sentry.io', 'stripe.com', 'stripe.network', 'swagger.io', 'tc39.es',
  'tealiumiq.com', 'testfairy.com', 'tiqcdn.com', 'unpkg.com', 'use.fontawesome.com',
  'use.typekit.net', 'w3.org', 'wappalyzer.com', 'whatwg.org', 'xml.org', 'xmlns.com',
  'xmlsoap.org', 'zdassets.com',
]);

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
