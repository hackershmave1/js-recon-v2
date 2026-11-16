// The team's additions to the built-in third-party rules (ADR-0010, R2): the extension keeps them in
// one place, downloads them at sign-in and capture start, falls back to its last saved copy, and the
// additions only ever ADD to what is dropped.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  getTeamRules, setTeamRules, resetTeamRules, loadSavedTeamRules, syncTeamRules, STORAGE_KEY,
} from '../modules/team-rules.js';
import { matchesDenylist, matchesPattern, globMatch } from '../modules/asset-classifier.js';
import { isNoiseHost } from '../modules/capture-scope.js';
import { SOURCE } from '../scripts/gen-third-party-rules.mjs';

const memory = (seed = {}) => {
  const m = { ...seed };
  return {
    m,
    async get(k) { return k in m ? { [k]: structuredClone(m[k]) } : {}; },
    async set(obj) { for (const [k, v] of Object.entries(obj)) m[k] = structuredClone(v); },
  };
};

// 1) Empty by default: the built-in behaviour is unchanged.
resetTeamRules();
assert.deepEqual(getTeamRules(), { hosts: [], filePatterns: [] });
assert.equal(isNoiseHost('cdn.tracker.example.com'), false);
assert.equal(matchesDenylist('https://app.acme.io/vendor/chat-widget.js', [], true), false);

// 2) Team additions only ADD: a host entry and a file pattern both start dropping, built-ins stay.
setTeamRules({ hosts: ['Tracker.Example.com'], filePatterns: ['/vendor/chat*.js'] });
assert.equal(isNoiseHost('cdn.tracker.example.com'), true);
assert.equal(isNoiseHost('nottracker.example.com'), false);
assert.equal(isNoiseHost('google-analytics.com'), true, 'a built-in entry is still dropped');
assert.equal(matchesDenylist('https://app.acme.io/vendor/chat-widget.js', [], true), true, 'a file pattern applies on any host');
assert.equal(matchesDenylist('https://app.acme.io/static/jquery-3.6.0.min.js', [], true), true);
// With the old default-profile switch off the team still applies (it is not that profile).
assert.equal(matchesDenylist('https://app.acme.io/vendor/chat-widget.js', [], false), true);

// 3) Junk is refused: non-strings, empties, oversized, too many.
setTeamRules({ hosts: [5, '', null, 'ok.example.com', 'x'.repeat(300)], filePatterns: ['', 'p'.repeat(500), 'good/path*'] });
assert.deepEqual(getTeamRules(), { hosts: ['ok.example.com'], filePatterns: ['good/path*'] });
setTeamRules({ hosts: Array.from({ length: 5000 }, (_, i) => `h${i}.example.com`), filePatterns: [] });
assert.ok(getTeamRules().hosts.length <= 500);
setTeamRules('garbage');
assert.deepEqual(getTeamRules(), { hosts: [], filePatterns: [] });

// 4) The matcher is linear: a hostile path against a many-wildcard pattern returns at once.
const t0 = Date.now();
assert.equal(matchesPattern('https://x.example.com/' + 'a'.repeat(5000) + '/nope', '/a*a*a*a*a/chat*.js'), false);
assert.ok(Date.now() - t0 < 500, 'no catastrophic backtracking');
// The same cases the platform runs (third_party_rules.json) still agree after the matcher change.
const doc = JSON.parse(readFileSync(SOURCE, 'utf8'));
for (const [url, expected] of doc.cases) {
  assert.equal(doc.file_patterns.some((p) => matchesPattern(url, p)), expected, url);
}
assert.equal(globMatch('/A*.JS', '/abc.js'), true);
assert.equal(globMatch('/a*', '/'), false);

// 5) Sync: a good download is applied AND saved.
resetTeamRules();
const store = memory();
const good = async () => ({ ok: true, data: { team_hosts: ['t1.example.com'], team_file_patterns: ['/p/widget*.js'] } });
let r = await syncTeamRules({ fetchRules: good, storageArea: store });
assert.equal(r.source, 'network');
assert.deepEqual(getTeamRules(), { hosts: ['t1.example.com'], filePatterns: ['/p/widget*.js'] });
assert.deepEqual(store.m[STORAGE_KEY], { hosts: ['t1.example.com'], filePatterns: ['/p/widget*.js'] });

// 6) A failed download keeps the last saved copy (a fresh worker loads it from storage first).
resetTeamRules();
await loadSavedTeamRules(store);
assert.deepEqual(getTeamRules().hosts, ['t1.example.com']);
for (const bad of [
  async () => ({ ok: false, status: 500 }),
  async () => ({ ok: false, error: 'timeout' }),
  async () => { throw new Error('network down'); },
  async () => ({ ok: true, data: null }),
]) {
  r = await syncTeamRules({ fetchRules: bad, storageArea: store });
  assert.equal(r.source, 'saved');
  assert.deepEqual(getTeamRules().hosts, ['t1.example.com'], 'the last saved copy still applies');
}

// 7) A removal on the platform propagates: the next good download replaces, it does not merge.
await syncTeamRules({ fetchRules: async () => ({ ok: true, data: { team_hosts: [], team_file_patterns: [] } }), storageArea: store });
assert.deepEqual(getTeamRules(), { hosts: [], filePatterns: [] });

// 8) Never blocks or throws: broken storage and a hanging download both return.
resetTeamRules();
const broken = { async get() { throw new Error('no storage'); }, async set() { throw new Error('no storage'); } };
assert.equal((await syncTeamRules({ fetchRules: good, storageArea: broken })).source, 'network');
const hang = () => new Promise(() => {});
const started = Date.now();
r = await syncTeamRules({ fetchRules: hang, storageArea: memory(), timeoutMs: 50 });
assert.equal(r.source, 'saved');
assert.ok(Date.now() - started < 1000);
await loadSavedTeamRules(broken); // does not throw

console.log('ok - team rules: add-only, sync, saved fallback, linear matcher');
