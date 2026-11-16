// Add and Ignore on an undecided site reach the platform (ADR-0010); the capture's own state follows.
import assert from 'node:assert/strict';
import { CaptureLifecycle, heldCounts } from '../modules/capture-lifecycle.js';

function storage(seed = {}) {
  const m = { ...seed };
  return {
    async get(k) { return k in m ? { [k]: structuredClone(m[k]) } : {}; },
    async set(obj) { for (const [k, v] of Object.entries(obj)) m[k] = structuredClone(v); },
  };
}

function harness({ decide = async () => ({ ok: true }), addScope = async () => ({ ok: true }), teamRule = async () => ({ ok: true, status: 201 }) } = {}) {
  const calls = [];
  const api = {
    start: async () => ({ ok: true, data: { runId: 'run-1', sessionId: 'plat-1' } }),
    end: async () => ({ ok: true, data: { ended: true } }),
    lookup: async () => ({ ok: true, data: { sessions: [] } }),
    addScope: async (sid, host) => { calls.push(['scope', sid, host]); return addScope(sid, host); },
    addTeamRule: async (kind, value) => { calls.push(['team', kind, value]); return teamRule(kind, value); },
    decideHost: async (sid, host, decision) => { calls.push(['decide', sid, host, decision]); return decide(sid, host, decision); },
  };
  const cs = { sessionId: 'ext-1', async startNew() {}, async adopt() {}, resetForCapture() {} };
  const gates = [];
  const lc = new CaptureLifecycle({
    local: storage(), session: storage(), api, captureSession: cs,
    getProcessing: () => 0, scheduleRetry: () => {},
    uploader: { flushAll: async () => {}, getStats: () => ({ pendingQueueLength: 0, isUploading: false, authPaused: false }) },
    teamRulesChanged: () => { calls.push(['team-changed']); },
    applyGate: async (g) => { gates.push(g); }, now: () => 1_000_000, sleep: async () => {},
  });
  return { lc, calls, gates };
}

async function started(h) {
  await h.lc.start({ scope: 'acme.io', mode: 'new', binding: { projectId: null } });
  h.lc.state.newSites.push('acme-static.com');
  h.lc.noteHeld('cdn.acme-static.com');
  h.lc.noteHeld('cdn.acme-static.com');
  h.lc.noteHeld('img.acme-static.com');
}

// The platform's session id is kept (the decision endpoints use it, not the extension's id).
{
  const h = harness();
  await started(h);
  assert.equal(h.lc.state.platformSessionId, 'plat-1');
  assert.deepEqual(h.lc.snapshot().capture.held, { 'acme-static.com': 3 });
  assert.deepEqual(heldCounts({ a: { x: 1, y: 2 } }), { a: 3 });
}

// Add: every held host under the site is released BEFORE the local scope changes.
{
  const h = harness();
  await started(h);
  const r = await h.lc.addToScope('acme-static.com');
  assert.equal(r.ok, true);
  const order = h.calls.map((c) => c[0]);
  assert.deepEqual(h.calls.filter((c) => c[0] === 'decide').map((c) => c.slice(1)),
    [['plat-1', 'cdn.acme-static.com', 'add'], ['plat-1', 'img.acme-static.com', 'add']]);
  assert.ok(order.lastIndexOf('decide') < order.indexOf('scope'), 'release first, then widen');
  assert.ok(h.lc.state.scope.includes('acme-static.com'));
  assert.deepEqual(h.lc.snapshot().capture.held, {}, 'nothing is held for an added site');
}

// A failed release leaves the site offered and the scope unchanged (not half-added).
{
  const h = harness({ decide: async () => ({ ok: false, status: 500, error: 'HTTP 500' }) });
  await started(h);
  const r = await h.lc.addToScope('acme-static.com');
  assert.equal(r.ok, false);
  assert.ok(!h.lc.state.scope.includes('acme-static.com'));
  assert.ok(h.lc.state.newSites.includes('acme-static.com'));
  assert.equal(h.calls.filter((c) => c[0] === 'scope').length, 0);
}

// A platform 404 (it holds nothing for that host) is not a failure.
{
  const h = harness({ decide: async () => ({ ok: false, status: 404 }) });
  await started(h);
  assert.equal((await h.lc.addToScope('acme-static.com')).ok, true);
}

// Ignore: the platform deletes what it holds; the site is on the ignore list from then on.
{
  const h = harness();
  await started(h);
  assert.equal(h.lc.isIgnoredSite('cdn.acme-static.com'), false);
  const r = await h.lc.dismissSite('acme-static.com');
  assert.equal(r.ok, true);
  assert.deepEqual(h.calls.filter((c) => c[0] === 'decide').map((c) => c[3]), ['ignore', 'ignore']);
  assert.equal(h.lc.isIgnoredSite('cdn.acme-static.com'), true, 'any host under the dismissed site');
  assert.equal(h.lc.isIgnoredSite('app.acme.io'), false);
  assert.deepEqual(h.lc.snapshot().capture.held, {});
  assert.ok(!h.lc.state.newSites.includes('acme-static.com'));
}

// A failed Ignore call is not fatal (the held content expires after 7 days); the researcher's
// choice still stands locally.
{
  const h = harness({ decide: async () => ({ ok: false, status: 500 }) });
  await started(h);
  const r = await h.lc.dismissSite('acme-static.com');
  assert.equal(r.ok, true);
  assert.equal(r.decisionPending, true);
  assert.equal(h.lc.isIgnoredSite('cdn.acme-static.com'), true);
}

// "Ignore for all my sessions": the site joins the TEAM list (apex host), then this session ignores it
// exactly as above, and the worker is told the team list changed.
{
  const h = harness();
  await started(h);
  const r = await h.lc.ignoreForAll('acme-static.com');
  assert.equal(r.ok, true);
  assert.deepEqual(h.calls.find((c) => c[0] === 'team'), ['team', 'host', 'acme-static.com']);
  assert.ok(h.calls.some((c) => c[0] === 'team-changed'));
  assert.deepEqual(h.calls.filter((c) => c[0] === 'decide').map((c) => c[3]), ['ignore', 'ignore']);
  assert.equal(h.lc.isIgnoredSite('cdn.acme-static.com'), true);
  assert.ok(!h.lc.state.newSites.includes('acme-static.com'));
}
// Already on the team list (409) is fine: the researcher's wish is already true.
{
  const h = harness({ teamRule: async () => ({ ok: false, status: 409 }) });
  await started(h);
  assert.equal((await h.lc.ignoreForAll('acme-static.com')).ok, true);
  assert.equal(h.lc.isIgnoredSite('cdn.acme-static.com'), true);
}
// A refused or failed team add changes nothing: the site is still offered.
for (const [res, error] of [[{ ok: false, status: 422, data: { detail: 'not a usable host' } }, 'not a usable host'],
                            [{ ok: false, status: 401 }, 'session_expired'],
                            [{ ok: false, error: 'unreachable' }, 'unreachable']]) {
  const h = harness({ teamRule: async () => res });
  await started(h);
  const r = await h.lc.ignoreForAll('acme-static.com');
  assert.equal(r.ok, false);
  assert.equal(r.error, error);
  assert.equal(h.lc.isIgnoredSite('cdn.acme-static.com'), false);
  assert.ok(h.lc.state.newSites.includes('acme-static.com'));
  assert.equal(h.calls.filter((c) => c[0] === 'decide').length, 0);
}
// No capture, no team rule.
{
  const h = harness();
  assert.deepEqual(await h.lc.ignoreForAll('acme-static.com'), { ok: false, error: 'no_capture' });
  assert.equal(h.calls.length, 0);
}

console.log('ok - capture lifecycle: Add and Ignore reach the platform');
