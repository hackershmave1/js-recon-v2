// modules/hook-matches.js — where the opt-in main-world response-body hook may run.
// It replaces window.fetch / XMLHttpRequest in the PAGE's own world, so it must run only where
// capture would: the in-scope hosts (the same exact/subdomain rule as isInScope). It used to be
// registered on <all_urls>: every site, out of scope or not — including Cloudflare challenge
// frames, whose own blocked `fetch('data:,')` probe Chrome then blamed on the extension.
import assert from 'node:assert/strict';
import { hookMatchPatterns } from '../modules/hook-matches.js';

const pass = (name) => console.log(`✓ ${name}`);
const on = (extra) => ({ captureResponseBodies: true, useDomainScope: true, includeSubdomains: true, ...extra });

assert.deepEqual(hookMatchPatterns({ captureResponseBodies: false, useDomainScope: true, domainScopes: ['acme.io'] }), []);
pass('setting off -> no hook anywhere');

assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['acme.io'] })), ['*://acme.io/*', '*://*.acme.io/*']);
pass('in-scope host and its subdomains only');

assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['acme.io'], includeSubdomains: false })), ['*://acme.io/*']);
pass('subdomains off -> the exact host only (as isInScope)');

assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['https://Shop.Acme.io:8443/x', 'api.acme.io'] })),
  ['*://shop.acme.io/*', '*://*.shop.acme.io/*', '*://api.acme.io/*', '*://*.api.acme.io/*']);
pass('scope entries normalized like the capture gate (scheme/port/path/case)');

assert.deepEqual(hookMatchPatterns(on({ useDomainScope: false, domainScopes: [] })), []);
assert.deepEqual(hookMatchPatterns(on({ domainScopes: [] })), []);
pass('no scope (fail-closed) -> no hook');

assert.deepEqual(hookMatchPatterns(on({ useDomainScope: false, domainScopes: [], captureEverything: true })), ['<all_urls>']);
pass('explicit "capture every tab" -> every site (the operator chose that)');

assert.deepEqual(hookMatchPatterns(on({ domainScopes: ['bad host', '[::1]', 'acme.io'] })), ['*://acme.io/*', '*://*.acme.io/*']);
pass('an entry that is not a valid match-pattern host is skipped (registration would throw)');

// Wiring (background.js is not importable in Node): the registration uses these patterns, an
// existing registration with other patterns is updated, and a new session re-syncs it.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const bg = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../background.js'), 'utf8');
const sync = bg.slice(bg.indexOf('async syncResponseBodyHook('), bg.indexOf('responseContentType(responseHeaders)'));
assert.match(sync, /const matches = hookMatchPatterns\(this\.settings\);/);
assert.doesNotMatch(sync, /<all_urls>/, 'the hook never hard-codes <all_urls>');
assert.match(sync, /updateContentScripts\(\[\{ id: 'recon-xhr-hook', matches \}\]\)/, 'an older <all_urls> registration is narrowed in place');
const newSession = bg.slice(bg.indexOf('async newSession('), bg.indexOf('async stopCapture('));
assert.match(newSession, /await this\.syncResponseBodyHook\(\);/, 'a new session re-syncs the hook to its scope');
pass('background wiring: scoped registration, in-place narrowing, re-sync on new session');
