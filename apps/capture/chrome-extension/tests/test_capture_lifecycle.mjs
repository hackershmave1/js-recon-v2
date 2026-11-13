// The worker-owned capture lifecycle: start/continue, pause/resume, a resumable End that answers
// first, browser-restart detection, and a capture the server ended.
import assert from 'node:assert/strict';
import { CaptureLifecycle, STATE_KEY, badgeText } from '../modules/capture-lifecycle.js';

function storage(seed = {}) {
  const m = { ...seed };
  return {
    _m: m,
    async get(k) { return k in m ? { [k]: structuredClone(m[k]) } : {}; },
    async set(obj) { for (const [k, v] of Object.entries(obj)) m[k] = structuredClone(v); },
  };
}

function harness({ local = storage(), session = storage(), api: apiOverrides = {}, stats, processing } = {}) {
  let t = 1_000_000;
  const calls = [];
  const gates = [];
  const uploaderStats = stats || { pendingQueueLength: 0, isUploading: false, authPaused: false };
  const api = {
    start: async (sid, body) => { calls.push(['start', sid, body]); return { ok: true, data: { runId: 'run-' + calls.length } }; },
    end: async (sid, body) => { calls.push(['end', sid, body]); return { ok: true, data: { ended: true } }; },
    lookup: async () => ({ ok: true, data: { sessions: [] } }),
    addScope: async (sid, host) => { calls.push(['scope', sid, host]); return { ok: true }; },
    ...apiOverrides,
  };
  const cs = {
    sessionId: 'sess-0',
    async startNew() { this.sessionId = 'sess-new'; calls.push(['rotate']); },
    async adopt(id, { binding }) { this.sessionId = id; calls.push(['adopt', id, binding]); },
    resetForCapture() { calls.push(['reset']); },
  };
  const retries = [];
  const proc = { n: processing || 0 };
  const lc = new CaptureLifecycle({
    local, session, api, captureSession: cs,
    getProcessing: () => proc.n,
    scheduleRetry: () => retries.push(t),
    uploader: { flushAll: async () => { calls.push(['flush']); }, getStats: () => uploaderStats },
    applyGate: async (g) => { gates.push(structuredClone(g)); },
    now: () => t,
    sleep: async () => {},
  });
  return { lc, calls, gates, local, session, advance: (ms) => { t += ms; }, uploaderStats, retries, proc };
}

// 1) New capture: fresh session, server run, gate opened on the scope.
{
  const h = harness();
  const r = await h.lc.start({ scope: 'acme.io', mode: 'new', binding: { projectId: 'eng-1' } });
  assert.equal(r.ok, true);
  assert.deepEqual(h.calls.map((c) => c[0]), ['rotate', 'start', 'reset']);
  assert.deepEqual(h.calls[1][2], { rootDomains: ['acme.io'], projectId: 'eng-1' });
  assert.deepEqual(h.gates.at(-1), { scope: ['acme.io'], capturing: true });
  assert.equal(h.lc.runIdForEnqueue(), r.runId);
  assert.ok(h.local._m[STATE_KEY].capture.runId, 'persisted');
}

// 2) Continue adopts the session AND its engagement; a second Start is refused while one runs.
{
  const h = harness();
  const match = { external_id: 'ext-old', engagement_id: 'eng-beta' };
  const r = await h.lc.start({ scope: 'acme.io', mode: 'continue', match });
  assert.equal(r.sessionId, 'ext-old');
  assert.deepEqual(h.calls[0], ['adopt', 'ext-old', { projectId: 'eng-beta', captureConfig: {}, overrideKeys: [] }]);
  assert.equal(h.calls[1][2].projectId, 'eng-beta');
  assert.equal((await h.lc.start({ scope: 'acme.io', mode: 'new' })).error, 'capture_in_progress');
}

// 3) Pause stops the clock and the gate; Resume restarts both.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  h.advance(5000);
  await h.lc.pause();
  h.advance(60000);
  assert.equal(h.lc.snapshot().capture.elapsedMs, 5000);
  assert.equal(h.lc.runIdForEnqueue(), null, 'nothing is stamped while paused');
  assert.deepEqual(h.gates.at(-1), { scope: ['acme.io'], capturing: false });
  await h.lc.resume();
  h.advance(1000);
  assert.equal(h.lc.snapshot().capture.elapsedMs, 6000);
}

// 4) End answers at once with ending:true, then drains and tells the platform, then closes.
{
  const h = harness();
  const { runId } = await h.lc.start({ scope: 'acme.io', mode: 'new' });
  const r = await h.lc.end();
  assert.deepEqual(r, { ok: true, ending: true });
  await h.lc.draining;
  await h.lc.chain;
  assert.ok(h.calls.some((c) => c[0] === 'end' && c[2].runId === runId));
  assert.equal(h.lc.snapshot().capture, null);
  assert.equal(h.lc.snapshot().lastEnded.runId, runId);
  assert.deepEqual(h.gates.at(-1), { scope: [], capturing: false }, 'scope cleared at End');
}

// 5) End with an expired sign-in keeps `ending` and resumes after login.
{
  const h = harness({ stats: { pendingQueueLength: 3, isUploading: false, authPaused: true } });
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  await h.lc.end();
  await h.lc.draining;
  assert.equal(h.lc.snapshot().capture.ending, true, 'still ending: uploads are paused for auth');
  assert.ok(!h.calls.some((c) => c[0] === 'end'), 'capture/end not called on a partial set');
  h.uploaderStats.authPaused = false;
  h.uploaderStats.pendingQueueLength = 0;
  h.lc.resumeAfterLogin();
  await h.lc.draining;
  await h.lc.chain;
  assert.equal(h.lc.snapshot().capture, null);
}

// 6) Browser restart (no boot marker) resumes PAUSED; a worker cold start (marker present) doesn't.
{
  const local = storage();
  const first = harness({ local });
  await first.lc.start({ scope: 'acme.io', mode: 'new' });

  const cold = harness({ local, session: storage({ captureBootMarker: 1 }) });
  await cold.lc.recover();
  assert.equal(cold.lc.snapshot().capture.paused, false, 'a 30 s worker respawn keeps capturing');

  const restarted = harness({ local, session: storage() });
  await restarted.lc.recover();
  assert.equal(restarted.lc.snapshot().capture.paused, true, 'a browser restart resumes paused');
  assert.deepEqual(restarted.gates.at(-1), { scope: ['acme.io'], capturing: false });
}

// 7) A pending End is resumed by a respawn.
{
  const local = storage();
  const first = harness({ local, stats: { pendingQueueLength: 2, isUploading: false, authPaused: true } });
  await first.lc.start({ scope: 'acme.io', mode: 'new' });
  await first.lc.end();
  await first.lc.draining;
  const again = harness({ local, session: storage({ captureBootMarker: 1 }) });
  await again.lc.recover();
  assert.ok(!again.calls.some((c) => c[0] === 'end'), 'recover() alone never ends: the outbox is not loaded yet');
  again.lc.resumePendingEnd();  // background.js calls this after rehydrate()
  await again.lc.draining;
  await again.lc.chain;
  assert.ok(again.calls.some((c) => c[0] === 'end'), 'the respawned worker finished the End');
  assert.equal(again.lc.snapshot().capture, null);
}

// 8) The server ended this capture (capture_ended): closed locally; another run's 409 is ignored.
{
  const h = harness();
  const { runId } = await h.lc.start({ scope: 'acme.io', mode: 'new' });
  await h.lc.endedRemotely('some-older-run');
  assert.ok(h.lc.snapshot().capture, 'an older run ending changes nothing');
  await h.lc.endedRemotely(runId);
  assert.equal(h.lc.snapshot().capture, null);
  assert.equal(h.lc.snapshot().lastEnded.reason, 'ended_elsewhere');
}

// 9) Add to scope: server first, then the gate widens; scope is the site's apex.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  const r = await h.lc.addToScope('cdn.beta.example');
  assert.deepEqual(r.scope, ['acme.io', 'beta.example']);
  assert.ok(h.calls.some((c) => c[0] === 'scope' && c[2] === 'beta.example'));
  assert.deepEqual(h.gates.at(-1), { scope: ['acme.io', 'beta.example'], capturing: true });
}

// 10) Resume never ends a capture on the local clock (the platform decides; its 409 closes it).
{
  const h = harness();
  const { runId } = await h.lc.start({ scope: 'acme.io', mode: 'new' });
  await h.lc.pause();
  h.advance(25 * 60 * 60 * 1000);
  assert.equal((await h.lc.resume()).ok, true, 'resumed: an overnight pause is not a dead capture');
  await h.lc.endedRemotely(runId);  // ...unless the platform says so
  assert.equal(h.lc.snapshot().capture, null);
}

// 11) A failed server start leaves no capture behind.
{
  const h = harness({ api: { start: async () => ({ ok: false, status: 401, error: 'HTTP 401' }) } });
  const r = await h.lc.start({ scope: 'acme.io', mode: 'new' });
  assert.deepEqual(r, { ok: false, error: 'session_expired' });
  assert.equal(h.lc.snapshot().capture, null);
}

// 12) New sites: a script from another registrable domain is noted once (by apex); in-scope hosts,
// known trackers/CDNs, and anything while paused or with "capture every tab" on are not.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  assert.equal(h.lc.noteSite('cdn.beta.example'), true);
  assert.equal(h.lc.noteSite('www.beta.example'), false, 'same site, already noted');
  assert.equal(h.lc.noteSite('api.acme.io'), false, 'in scope');
  assert.equal(h.lc.noteSite('www.google-analytics.com'), false, 'known tracker');
  assert.equal(h.lc.noteSite('gamma.example', { captureEverything: true }), false, 'every tab: no badge');
  assert.deepEqual(h.lc.snapshot().capture.newSites, ['beta.example']);
  await h.lc.dismissSite('beta.example');
  assert.deepEqual(h.lc.snapshot().capture.newSites, []);
  await h.lc.pause();
  assert.equal(h.lc.noteSite('delta.example'), false, 'nothing is noted while paused');
}

// 13) Badge: the file count, plus a dot while a new site is unanswered — paused too.
assert.equal(badgeText({ capturing: true, files: 12, newSites: 0 }), '12');
assert.equal(badgeText({ capturing: true, files: 12, newSites: 1 }), '12•');
assert.equal(badgeText({ capturing: true, files: 1500, newSites: 1 }), '99+•');
assert.equal(badgeText({ capturing: false, files: 12, newSites: 1 }), '•');
assert.equal(badgeText({ capturing: false, files: 12, newSites: 0 }), '');
assert.equal(badgeText({ capturing: true, files: 1500, newSites: 0 }), '999+');

// 14) End waits for captured requests still being processed, not just the upload queue.
{
  const h = harness({ processing: 2 });
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  await h.lc.end();
  let spins = 0;
  h.lc.sleep = async () => { spins += 1; if (spins === 3) h.proc.n = 0; };
  await h.lc.draining;
  await h.lc.chain;
  assert.ok(spins >= 3, 'kept waiting while files were being processed');
  assert.ok(h.calls.some((c) => c[0] === 'end'));
}

// 15) Past the drain deadline End does NOT end on a partial set: it stays ending and retries later.
{
  const h = harness({ stats: { pendingQueueLength: 5, isUploading: false, authPaused: false } });
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  h.lc.sleep = async () => h.advance(60 * 1000);
  await h.lc.end();
  await h.lc.draining;
  assert.equal(h.lc.snapshot().capture.ending, true);
  assert.ok(!h.calls.some((c) => c[0] === 'end'));
  assert.equal(h.retries.length, 1, 'a retry is armed');
}

// 16) A failed capture/end (network) arms a retry instead of hanging in "Ending…".
{
  const h = harness({ api: { end: async () => ({ ok: false, error: 'unreachable' }) } });
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  await h.lc.end();
  await h.lc.draining;
  assert.equal(h.lc.snapshot().capture.ending, true);
  assert.equal(h.retries.length, 1);
}

// 17) Logout with no lifecycle capture leaves the (legacy) gate alone.
{
  const h = harness();
  await h.lc.forget();
  assert.equal(h.gates.length, 0, 'no gate write without a capture');
}

// 18) A dismissed site stays dismissed for this capture.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  h.lc.noteSite('beta.example');
  await h.lc.dismissSite('beta.example');
  assert.equal(h.lc.noteSite('cdn.beta.example'), false);
  assert.deepEqual(h.lc.snapshot().capture.newSites, []);
}

// 19) The scope is normalized like every scope write (no gate entry that matches nothing).
{
  const h = harness();
  await h.lc.start({ scope: 'https://www.Acme.io/login', mode: 'new' });
  assert.deepEqual(h.gates.at(-1).scope, ['acme.io']);
  assert.equal((await harness().lc.start({ scope: '  ', mode: 'new' })).error, 'no_scope');
}

// 20) Resume on a capture that isn't paused loses no time; a restart counts only up to the last file.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  h.advance(10_000);
  await h.lc.resume();
  assert.equal(h.lc.snapshot().capture.elapsedMs, 10_000);

  const local = storage();
  const day = harness({ local });
  await day.lc.start({ scope: 'acme.io', mode: 'new' });
  day.advance(2 * 60_000);
  day.lc.noteBatch();  // last captured file at +2 min (persisted: >= 1 min since the last write)
  const night = harness({ local, session: storage() });
  night.advance(12 * 60 * 60 * 1000);  // browser closed overnight
  await night.lc.recover();
  assert.equal(night.lc.snapshot().capture.elapsedMs, 2 * 60_000, 'closed hours are not counted');
}

// 21) The popup's header: the snapshot says how the capture began (new / continue + its number in
// the session) and whether a browser restart paused it; Resume clears that.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'continue', match: { external_id: 'ext-old', engagement_id: null, captures: 4 } });
  assert.equal(h.lc.snapshot().capture.mode, 'continue');
  assert.equal(h.lc.snapshot().capture.ordinal, 5);

  const local = storage();
  const before = harness({ local });
  await before.lc.start({ scope: 'acme.io', mode: 'new' });
  assert.equal(before.lc.snapshot().capture.mode, 'new');
  assert.equal(before.lc.snapshot().capture.ordinal, 1);
  const after = harness({ local, session: storage() });
  await after.lc.recover();
  assert.equal(after.lc.snapshot().capture.pausedByRestart, true);
  await after.lc.resume();
  assert.equal(after.lc.snapshot().capture.pausedByRestart, false);
}

// 22) "Done" on the Capture-ended banner forgets it (persisted), and only it.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'new' });
  await h.lc.end();
  await h.lc.draining;
  await h.lc.chain;
  assert.ok(h.lc.snapshot().lastEnded);
  assert.deepEqual(await h.lc.dismissEnded(), { ok: true });
  assert.equal(h.lc.snapshot().lastEnded, null);
  assert.equal(h.local._m[STATE_KEY].lastEnded, null);
}

console.log('test_capture_lifecycle: ok');
