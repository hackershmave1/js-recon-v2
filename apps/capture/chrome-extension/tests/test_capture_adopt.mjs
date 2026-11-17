// Continue adopts a session started in the workspace (ADR-0012): the server mints the extension id once;
// the adopt runs inside the serialized Start, after the capture_in_progress check.
import assert from 'node:assert/strict';
import { CaptureLifecycle } from '../modules/capture-lifecycle.js';
import { continueOptions, startErrorText } from '../src/popup/viewmodels/captureVm.js';

function storage(seed = {}) {
  const m = { ...seed };
  return {
    async get(k) { return k in m ? { [k]: structuredClone(m[k]) } : {}; },
    async set(obj) { for (const [k, v] of Object.entries(obj)) m[k] = structuredClone(v); },
  };
}

function harness({ adopt, start } = {}) {
  const calls = [];
  const api = {
    adopt: async (id) => { calls.push(['adopt', id]); return adopt ? adopt(id) : { ok: true, status: 200, data: { sessionId: id, externalId: 'minted-ext-1', engagementId: 'eng-9' } }; },
    start: async (sid, body) => { calls.push(['start', sid, body]); return start ? start(sid, body) : { ok: true, data: { runId: 'run-1', sessionId: 'plat-1' } }; },
    end: async () => ({ ok: true, data: { ended: true } }),
    lookup: async () => ({ ok: true, data: { sessions: [], workspace: [] } }),
    addScope: async () => ({ ok: true }),
    decideHost: async () => ({ ok: true }),
  };
  const cs = {
    sessionId: 'sess-0',
    async startNew() { calls.push(['rotate']); },
    async adopt(id, { binding }) { this.sessionId = id; calls.push(['local-adopt', id, binding.projectId]); },
    resetForCapture() {},
  };
  const lc = new CaptureLifecycle({
    local: storage(), session: storage(), api, captureSession: cs,
    getProcessing: () => 0, scheduleRetry: () => {},
    uploader: { flushAll: async () => {}, getStats: () => ({ pendingQueueLength: 0, isUploading: false, authPaused: false }) },
    applyGate: async () => {}, now: () => 1_000_000, sleep: async () => {},
  });
  return { lc, calls };
}
const workspaceMatch = { workspace_session_id: 'plat-1', engagement_id: 'eng-9', captures: 0 };

// 1) Adopt first, then the local session switch, then Start with the MINTED id and the session's engagement.
{
  const h = harness();
  const r = await h.lc.start({ scope: 'acme.io', mode: 'continue', match: workspaceMatch });
  assert.equal(r.ok, true);
  assert.deepEqual(h.calls.map((c) => c[0]), ['adopt', 'local-adopt', 'start']);
  assert.deepEqual(h.calls[0], ['adopt', 'plat-1']);
  assert.deepEqual(h.calls[1], ['local-adopt', 'minted-ext-1', 'eng-9']);
  assert.equal(h.calls[2][1], 'minted-ext-1');
  assert.equal(h.calls[2][2].projectId, 'eng-9', 'the engagement is the session\'s own, never the client\'s choice');
  assert.equal(h.lc.state.mode, 'continue');
}

// 2) An ordinary capture session (it has an extension id) is continued with NO adopt call.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'continue', match: { external_id: 'ext-5', engagement_id: null, captures: 2 } });
  assert.ok(!h.calls.some((c) => c[0] === 'adopt'));
  assert.deepEqual(h.calls[0], ['local-adopt', 'ext-5', null]);
}

// 3) A refused Start (a capture already running) never burns an adopt.
{
  const h = harness();
  await h.lc.start({ scope: 'acme.io', mode: 'new', binding: null });
  const second = await h.lc.start({ scope: 'other.io', mode: 'continue', match: workspaceMatch });
  assert.equal(second.error, 'capture_in_progress');
  assert.ok(!h.calls.some((c) => c[0] === 'adopt'), 'adopt happens after the in-progress check');
}

// 4) Another caller got there first (409): nothing is switched or started, and the popup is told to re-read.
{
  const h = harness({ adopt: async () => ({ ok: false, status: 409, error: 'HTTP 409', data: { detail: { error: 'already_adopted' } } }) });
  const r = await h.lc.start({ scope: 'acme.io', mode: 'continue', match: workspaceMatch });
  assert.deepEqual([r.ok, r.error, r.code], [false, 'already_adopted', 'already_adopted']);
  assert.deepEqual(h.calls.map((c) => c[0]), ['adopt']);
  assert.equal(h.lc.state, null);
}

// 5) Signed out (401) is an expired session; a vanished session (404) is no longer available.
{
  const h401 = harness({ adopt: async () => ({ ok: false, status: 401, error: 'HTTP 401', data: null }) });
  assert.equal((await h401.lc.start({ scope: 'acme.io', mode: 'continue', match: workspaceMatch })).error, 'session_expired');
  const h404 = harness({ adopt: async () => ({ ok: false, status: 404, error: 'HTTP 404', data: null }) });
  assert.equal((await h404.lc.start({ scope: 'acme.io', mode: 'continue', match: workspaceMatch })).error, 'no_match');
}

// 6) Adopt succeeded but capture/start fails: the id is permanent, so the failure is reported and no
//    capture state is left behind (the session now appears in the normal Continue list).
{
  const h = harness({ start: async () => ({ ok: false, status: 500, error: 'HTTP 500' }) });
  const r = await h.lc.start({ scope: 'acme.io', mode: 'continue', match: workspaceMatch });
  assert.equal(r.ok, false);
  assert.equal(h.lc.state, null);
  assert.deepEqual(h.calls.slice(0, 2).map((c) => c[0]), ['adopt', 'local-adopt']);
}

// 7) A match with neither id is refused.
{
  const h = harness();
  assert.equal((await h.lc.start({ scope: 'acme.io', mode: 'continue', match: { captures: 0 } })).error, 'no_match');
  assert.deepEqual(h.calls, []);
}

// 8) The popup offers workspace sessions as Continue options, labelled as started in the workspace.
{
  const options = continueOptions(
    [{ external_id: 'ext-5', engagement_id: null, captures: 2, largest: null }],
    [],
    'acme.io',
    [{ session_id: 'plat-1', engagement_id: null, name: 'Acme crawl', runs: 3 }, { session_id: 'x', external_id: 'leak', runs: 1 }],
  );
  assert.equal(options.length, 2, 'the capture session and ONE adoptable (an entry carrying an id is never offered as adoptable)');
  const ws = options.find((o) => o.workspace);
  assert.equal(ws.key, 'ws:plat-1');
  assert.deepEqual(ws.match, { workspace_session_id: 'plat-1', engagement_id: null, captures: 0 });
  assert.match(ws.line, /started in the workspace · 3 runs/);
  assert.equal(continueOptions([], [], 'acme.io', undefined).length, 0);
}

assert.match(startErrorText({ error: 'already_adopted' }), /taken by another capture/);
assert.match(startErrorText({ error: 'no_match' }), /no longer available/);

console.log('ok - continue adopts a workspace session');
