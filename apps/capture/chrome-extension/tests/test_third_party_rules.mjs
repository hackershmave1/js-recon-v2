// The built-in third-party rules (ADR-0010): ONE source (the platform's third_party_rules.json), a
// generated copy here that must not drift, the shared cases, and what a host entry versus a file
// pattern may drop.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { renderModule, SOURCE, TARGET } from '../scripts/gen-third-party-rules.mjs';
import { NOISE_HOSTS, FILE_PATTERNS } from '../modules/third-party-rules.js';
import { matchesPattern, matchesDenylist, DEFAULT_PROFILE_PATTERNS } from '../modules/asset-classifier.js';
import { isNoiseHost } from '../modules/capture-scope.js';
import { ADMIT, admitScript } from '../modules/script-admission.js';

const doc = JSON.parse(readFileSync(SOURCE, 'utf8'));

// 1) No drift: the committed module is exactly what the generator renders from the JSON.
assert.equal(readFileSync(TARGET, 'utf8'), renderModule(doc),
  'modules/third-party-rules.js is stale: run `node scripts/gen-third-party-rules.mjs`');
assert.deepEqual([...NOISE_HOSTS].sort(), [...new Set(Object.values(doc.hosts).flat())].sort());
assert.deepEqual(FILE_PATTERNS, doc.file_patterns);

// 2) The shared cases run through THIS matcher; the platform runs the same ones through its own
//    (recon/findings/third_party_rules_test.py), so the two cannot disagree.
const fileMatch = (url) => FILE_PATTERNS.some((p) => matchesPattern(url, p));
for (const [url, expected] of doc.cases) {
  assert.equal(fileMatch(url), expected, `${url} should ${expected ? '' : 'not '}match a third-party file pattern`);
}

// 3) A file pattern applies on any host: the app's own, an unscoped one, and with a query string.
assert.equal(matchesDenylist('https://app.acme.io/static/jquery-3.6.0.min.js?v=2', [], true), true);
assert.equal(matchesDenylist('http://localhost:3000/js/jquery.min.js', [], true), true);
assert.equal(matchesDenylist('https://app.acme.io/static/app.js', [], true), false);
assert.ok(FILE_PATTERNS.every((p) => DEFAULT_PROFILE_PATTERNS.includes(p)), 'the default profile carries every file pattern');

// 4) What the admission rule does with them: a file pattern drops on a scoped host; a host entry never
//    drops a host the researcher typed into the scope, and drops an unscoped one.
const page = { pageKnown: true, pageInScope: true };
assert.equal(admitScript({ ...page, scriptInScope: true, thirdPartyFile: true }), ADMIT.DROP_THIRD_PARTY);
assert.equal(admitScript({ ...page, scriptInScope: false, thirdPartyFile: true }), ADMIT.DROP_THIRD_PARTY);
assert.equal(admitScript({ ...page, scriptInScope: true, thirdPartyHost: true }), ADMIT.CAPTURE, 'a typed scope host is never dropped by a host entry');
assert.equal(admitScript({ ...page, scriptInScope: false, thirdPartyHost: true }), ADMIT.DROP_THIRD_PARTY);

// 5) Host entries: exact or dot-suffix, never a bare suffix (the existing behaviour).
assert.equal(isNoiseHost('cdn.mxpnl.com'), true);
assert.equal(isNoiseHost('google-analytics.com'), true);
assert.equal(isNoiseHost('notamplitude.com'), false);
assert.equal(isNoiseHost('app.acme.io'), false);

console.log('ok - third-party rules: one source, no drift, shared cases, host versus file');
