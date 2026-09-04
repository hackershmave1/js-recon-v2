// normalize-scope.js — reduce user-supplied scope entries (full URL, host:port, user@host,
// www.host, *.host) to bare hostnames so they gate capture (domainScopes) and match the backend's
// normalize_root_domains. Pure (no chrome/DOM) so background imports it and a Node test covers it.
//
// D40: a leading `*.` is now stripped. The popup's own placeholder used to suggest `*.target.com`,
// but isInScope only does exact/suffix matching — a literal `*.target.com` matched no host, so the
// operator could set a scope and still capture zero. `*.target.com` now normalizes to `target.com`
// (subdomains are covered by the includeSubdomains toggle, not a wildcard token).
export function normalizeRootDomains(values) {
  const list = Array.isArray(values) ? values : [];
  const out = [];
  for (const value of list) {
    let host = String(value || '').trim().toLowerCase();
    if (!host) continue;
    host = host.replace(/^[a-z][a-z0-9+.-]*:\/\//, ''); // scheme
    host = host.replace(/^[^@/]*@/, '');                // userinfo
    host = host.split('/')[0].split('?')[0].split('#')[0].split(':')[0]; // path/query/frag/port
    if (host.startsWith('www.')) host = host.slice(4);
    if (host.startsWith('*.')) host = host.slice(2);    // wildcard prefix -> bare root domain
    if (host && !out.includes(host)) out.push(host);
  }
  return out;
}
