// modules/hook-matches.js — the one capture-scope host rule, and where the opt-in main-world
// response-body hook may run. The hook replaces window.fetch / XMLHttpRequest in the PAGE's own
// world, so it must run only where capture would. It used to be registered on <all_urls>: every
// site — including Cloudflare challenge frames, whose own blocked `fetch('data:,')` probe Chrome
// then blamed on the extension.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { hookMatchPatterns, hostInScope } from '../modules/hook-matches.js';

const pass = (name) => console.log(`✓ ${name}`);
const on = (extra) => ({ captureResponseBodies: true, useDomainScope: true, includeSubdomains: true, ...extra });

// --- patterns ---------------------------------------------------------------------------------
assert.deepEqual(hookMatchPatterns({ captureResponseBodies: false, useDomainScope: true, domainScopes: ['acme.io'] }), []);
pass('setting off -> no hook anywhere');
assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['acme.io'] })), ['*://acme.io/*', '*://*.acme.io/*']);
pass('in-scope host and its subdomains only');
assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['acme.io'], includeSubdomains: false })), ['*://acme.io/*']);
pass('subdomains off -> the exact host only');
assert.deepEqual(hookMatchPatterns(on({ domainScopes: [] })), []);
assert.deepEqual(hookMatchPatterns(on({ useDomainScope: false, domainScopes: ['acme.io'] })), []);
pass('no scope (fail-closed) -> no hook');
assert.deepEqual(hookMatchPatterns(on({ useDomainScope: false, domainScopes: [], captureEverything: true })), ['<all_urls>']);
assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['acme.io'], captureEverything: true })), ['*://acme.io/*', '*://*.acme.io/*']);
pass('"capture every tab" -> every site only when no scope is set (as capture)');
assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['10.0.0.5', 'localhost', 'dev_api.acme.io'] })),
  ['*://10.0.0.5/*', '*://localhost/*', '*://*.localhost/*', '*://dev_api.acme.io/*', '*://*.dev_api.acme.io/*']);
pass('IPv4 (no subdomain pattern), localhost, underscore hosts');
assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['bad host', '[::1]', '*.legacy.io', 'acme.io'] })), ['*://acme.io/*', '*://*.acme.io/*']);
assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['', ' '], captureEverything: true })), []);
pass('entries that are not valid pattern hosts are skipped (and match nothing in capture either)');

// --- the hook covers EXACTLY the hosts capture would (one rule, table-driven) -----------------
const patternMatches = (pattern, host) => {
  if (pattern === '<all_urls>') return true;
  const h = pattern.slice('*://'.length, pattern.indexOf('/', '*://'.length));
  return h.startsWith('*.') ? host.endsWith(h.slice(1)) : host === h;
};
const scopes = [
  on({ domainScopes: ['acme.io'] }),
  on({ domainScopes: ['acme.io'], includeSubdomains: false }),
  on({ domainScopes: ['www.acme.io'], includeSubdomains: false }),
  on({ domainScopes: ['*.acme.io', 'https://acme.io/'] }),        // pre-D40 stored entries
  on({ domainScopes: ['', ' '], captureEverything: true }),
  on({ useDomainScope: false, domainScopes: [], captureEverything: true }),
  on({ useDomainScope: false, domainScopes: [] }),
  on({ domainScopes: ['10.0.0.5', 'dev_api.acme.io'] }),
];
const hosts = ['acme.io', 'www.acme.io', 'api.acme.io', 'evil-acme.io', 'other.test',
  'challenges.cloudflare.com', '10.0.0.5', 'dev_api.acme.io', 'x.dev_api.acme.io'];
for (const settings of scopes) {
  const patterns = hookMatchPatterns(settings);
  for (const host of hosts) {
    const hooked = patterns.some((p) => patternMatches(p, host));
    assert.equal(hooked, hostInScope(settings, host),
      `${JSON.stringify(settings.domainScopes)} / ${host}: hook ${hooked} vs capture ${hostInScope(settings, host)}`);
  }
}
pass(`hook hosts == capture hosts across ${scopes.length} scopes x ${hosts.length} hosts`);

// --- wiring (background.js is not importable in Node) -----------------------------------------
const bg = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../background.js'), 'utf8');
assert.match(bg, /return hostInScope\(this\.settings, new URL\(url\)\.hostname\);/, 'isInScope uses the shared rule');
const sync = bg.slice(bg.indexOf('syncResponseBodyHook() {'), bg.indexOf('responseContentType(responseHeaders)'));
assert.match(sync, /this\.hookSync = \(this\.hookSync \|\| Promise\.resolve\(\)\)\.then\(\(\) => this\.applyResponseBodyHook\(\)\);/,
  'syncs are serialized');
assert.match(sync, /async applyResponseBodyHook\(\) \{\s*const matches = hookMatchPatterns\(this\.settings\);/,
  'each queued sync recomputes from the settings current when it runs');
assert.doesNotMatch(sync, /<all_urls>/, 'the hook never hard-codes <all_urls>');
assert.match(sync, /updateContentScripts\(\[\{ id: 'recon-xhr-hook', matches \}\]\)/, 'an older registration is narrowed in place');
assert.match(sync, /console\.warn\('response-body hook sync failed', e\)/, 'a failed sync is logged, not silent');
const newSession = bg.slice(bg.indexOf('async newSession('), bg.indexOf('async stopCapture('));
assert.match(newSession, /await this\.syncResponseBodyHook\(\);/, 'a new session re-syncs the hook to its scope');
const updateSettings = bg.slice(bg.indexOf('async updateSettings('), bg.indexOf('async updateSettings(') + 1500);
assert.match(updateSettings, /this\.syncResponseBodyHook\(\);/, 'a settings change re-syncs the hook');
pass('background wiring: shared rule, serialized recomputing syncs, logged failures, both re-sync sites');
