// Unit tests for the pure scope normalizer (modules/normalize-scope.js). Pure module, imported
// directly. The D40 case is the leading `*.` strip: the popup's own placeholder used to suggest
// `*.target.com`, which isInScope (exact/suffix only) never matched — silent no-op capture.
import assert from 'node:assert/strict';
import { normalizeRootDomains } from '../modules/normalize-scope.js';

function test_strips_wildcard_prefix() {
  assert.deepEqual(normalizeRootDomains(['*.target.com']), ['target.com'], 'D40: *. prefix stripped to bare root');
  assert.deepEqual(normalizeRootDomains(['https://*.target.com/app']), ['target.com'], 'scheme + *. together');
  assert.deepEqual(normalizeRootDomains(['*.sub.target.com']), ['sub.target.com'], 'only the leading *. is removed');
}

function test_existing_normalizations_kept() {
  assert.deepEqual(normalizeRootDomains(['https://target.com/path?q=1#f']), ['target.com'], 'scheme/path/query/frag stripped');
  assert.deepEqual(normalizeRootDomains(['user@target.com:8443']), ['target.com'], 'userinfo + port stripped');
  assert.deepEqual(normalizeRootDomains(['www.target.com']), ['target.com'], 'www. stripped');
  assert.deepEqual(normalizeRootDomains(['TARGET.com']), ['target.com'], 'lowercased');
}

function test_plain_host_unchanged() {
  assert.deepEqual(normalizeRootDomains(['app.target.com']), ['app.target.com'], 'a bare host is left alone');
}

function test_dedupe_and_empties() {
  assert.deepEqual(
    normalizeRootDomains(['*.target.com', 'target.com', '  ', '', 'www.target.com']),
    ['target.com'],
    'wildcard/www/exact all collapse to one, blanks dropped'
  );
}

function test_non_array_and_junk() {
  assert.deepEqual(normalizeRootDomains(null), [], 'non-array => []');
  assert.deepEqual(normalizeRootDomains(undefined), [], 'undefined => []');
  assert.deepEqual(normalizeRootDomains(['*.', ' ', null]), [], 'degenerate entries produce nothing, never throw');
}

const tests = [
  test_strips_wildcard_prefix,
  test_existing_normalizations_kept,
  test_plain_host_unchanged,
  test_dedupe_and_empties,
  test_non_array_and_junk
];

let passed = 0;
for (const t of tests) { t(); passed += 1; }
console.log(`normalize-scope: ${passed}/${tests.length} passed`);
