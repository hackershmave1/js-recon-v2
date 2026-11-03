// D46(c): capture history rules (modules/capture-history.js). Pure module, imported directly, so
// these tests exercise behaviour rather than background.js source text. Covers the two bugs the
// old regex-only test let through: the summary cache never filled (wrong response shape), and a
// cached summary or a history row could surface under the wrong session or the wrong tenant.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import {
  HISTORY_MAX,
  summaryToCache,
  summaryForSession,
  addToHistory,
  historyForTenant,
} from '../modules/capture-history.js';

const complete = { status: 'complete', counts: { total: 17, endpoints: 12, secrets: 5 } };

function test_caches_the_client_response_shape() {
  // WorkspaceClient.getSessionFindingsSummary returns { success, summary } — not the summary itself.
  const cached = summaryToCache('s1', { success: true, summary: complete });
  assert.deepEqual(cached, { sessionId: 's1', summary: complete });
}

function test_does_not_cache_incomplete_or_failed() {
  assert.equal(summaryToCache('s1', { success: true, summary: { status: 'running' } }), null);
  assert.equal(summaryToCache('s1', { success: false, status: 404 }), null);
  assert.equal(summaryToCache('s1', null), null);
  assert.equal(summaryToCache('', { success: true, summary: complete }), null, 'no session id => nothing to key on');
}

function test_summary_only_attaches_to_its_own_session() {
  const cached = summaryToCache('s1', { success: true, summary: complete });
  assert.deepEqual(summaryForSession(cached, 's1'), complete);
  // Logout rotates s1 -> s2 without a history save; s2's row must not inherit s1's counts.
  assert.equal(summaryForSession(cached, 's2'), null);
  assert.equal(summaryForSession(null, 's1'), null);
}

function test_add_to_history_prepends_and_caps() {
  let history = [];
  for (let i = 0; i < HISTORY_MAX + 3; i++) history = addToHistory(history, { sessionId: `s${i}` });
  assert.equal(history.length, HISTORY_MAX);
  assert.equal(history[0].sessionId, `s${HISTORY_MAX + 2}`, 'newest first');
  assert.deepEqual(addToHistory('not-an-array', { sessionId: 'x' }), [{ sessionId: 'x' }], 'corrupt storage resets');
}

function test_history_shows_only_the_current_tenant() {
  const history = [
    { sessionId: 'a1', tenantId: 'tenant-a' },
    { sessionId: 'b1', tenantId: 'tenant-b' },
    { sessionId: 'anon', tenantId: '' },
    { sessionId: 'legacy' }, // pre-tagging row: belongs to no tenant, so hidden everywhere
  ];
  assert.deepEqual(historyForTenant(history, 'tenant-a').map((e) => e.sessionId), ['a1']);
  assert.deepEqual(historyForTenant(history, 'tenant-b').map((e) => e.sessionId), ['b1']);
  assert.deepEqual(historyForTenant(history, '').map((e) => e.sessionId), ['anon']);
  assert.deepEqual(historyForTenant(history, undefined).map((e) => e.sessionId), ['anon']);
  assert.deepEqual(historyForTenant(null, 'tenant-a'), []);
}

// Wiring that only the source can show: background.js routes the cache, the save and the read
// through this module and clears the cache on every reset path.
function test_background_uses_the_module() {
  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
  assert.ok(bg.includes("from './modules/capture-history.js'"), 'imports capture-history');
  assert.ok(/summaryToCache\(req\.sessionId,\s*result\)/.test(bg), 'handler caches via summaryToCache keyed by the requested session');
  assert.ok(/summaryForSession\(this\._lastFindingsSummary,\s*this\.sessionId\)/.test(bg), 'save attaches only this session\'s summary');
  assert.ok(/historyForTenant\(/.test(bg), 'getHistory filters by tenant');
  const resets = (bg.match(/this\._lastFindingsSummary = null;/g) || []).length;
  assert.ok(resets >= 4, `cache cleared in constructor + newSession + resetCaptureSession + clearFiles (found ${resets})`);
}

test_caches_the_client_response_shape();
test_does_not_cache_incomplete_or_failed();
test_summary_only_attaches_to_its_own_session();
test_add_to_history_prepends_and_caps();
test_history_shows_only_the_current_tenant();
test_background_uses_the_module();
console.log('ok - D46(c) capture history: session-keyed summary cache + tenant-scoped rows');
