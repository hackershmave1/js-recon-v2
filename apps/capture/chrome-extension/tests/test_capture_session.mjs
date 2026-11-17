// CaptureSession (modules/capture-session.js): the per-session state, its persistence and the three
// session transitions, tested through the module's interface with in-memory fakes. Replaces the
// regex-on-source checks of background.js's reset paths — these assert what happens, in what order.
import assert from 'node:assert/strict';
import { CaptureSession } from '../modules/capture-session.js';

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    async get(key) { return key in data ? { [key]: data[key] } : {}; },
    async set(obj) { Object.assign(data, obj); },
    async remove(key) { delete data[key]; },
  };
}

function makeSession({ storage = fakeStorage(), settings = {}, dedupEntries = [] } = {}) {
  const calls = [];
  let n = 0;
  const sessionStore = {
    generate: () => 'placeholder',
    async loadOrCreate() { calls.push('session.load'); return 's0'; },
    // Like SessionStore: whatever travels with the id is written in the same storage write.
    async rotate(alongside = {}) {
      calls.push('session.rotate');
      const id = `s${++n}`;
      await storage.set({ ...alongside, reconSessionId: id });
      return id;
    },
  };
  const dedupStore = {
    async getAll() { return dedupEntries; },
    async clear() { calls.push('dedup.clear'); },
  };
  const uploader = {
    config: undefined,
    async flushAll() { calls.push('uploader.flushAll'); },
    setConfig(config) { calls.push(`uploader.setConfig:${config ? config.projectId : 'null'}`); this.config = config; },
    async clearOutbox() { calls.push('uploader.clearOutbox'); },
    getStats() { return { projectId: this.config?.projectId || null }; },
  };
  const authTracker = { clear() { calls.push('auth.clear'); } };
  const session = new CaptureSession({
    storage, sessionStore, dedupStore, uploader, authTracker,
    getSettings: () => settings, observationCap: 3,
  });
  return { session, calls, storage, uploader };
}

function capture(session, url, extra = {}) {
  session.capturedFiles.set(url, { url, contentLength: 10, ...extra });
  session.capturedHashes.set(`h-${url}`, { url });
}

async function test_start_new_flushes_then_rotates_and_binds() {
  const { session, calls, storage } = makeSession({ settings: { authTenantId: 'tenant-a', domainScopes: ['acme.com'] } });
  await session.restore();
  capture(session, 'https://acme.com/app.js', { hasSourceMap: true, secretCount: 2 });
  session.processingQueue.push({ url: 'straggler' });
  calls.length = 0;

  const binding = { projectId: 'p1', captureConfig: {}, overrideKeys: [] };
  await session.startNew({ binding });

  // Flush the old session's batches before its id is gone; the live binding switches with the id.
  assert.deepEqual(calls, ['uploader.flushAll', 'session.rotate', 'uploader.setConfig:p1', 'dedup.clear', 'auth.clear']);
  assert.equal(session.sessionId, 's1');
  assert.equal(session.capturedFiles.size, 0);
  assert.equal(session.capturedHashes.size, 0);
  assert.equal(session.processingQueue.length, 0, 'stragglers dropped so they cannot be stamped with the new id');
  assert.deepEqual(storage.data.pendingSessionConfig, binding, 'binding survives a respawn');
  assert.equal(storage.data.captureHistory, undefined, 'no local capture history is kept (the workspace has it)');
}

async function test_start_standalone_unbinds_and_optionally_drops_outbox() {
  const storage = fakeStorage({ pendingSessionConfig: { projectId: 'p1' } });
  const { session, calls } = makeSession({ storage });
  await session.restore();
  capture(session, 'https://acme.com/app.js');
  session.processingStats.failedFiles = 3;
  calls.length = 0;

  await session.startStandalone({ dropOutbox: false });
  assert.deepEqual(calls, ['session.rotate', 'uploader.setConfig:null', 'dedup.clear', 'auth.clear']);
  assert.equal(storage.data.pendingSessionConfig, undefined, 'a respawn cannot re-bind the old engagement');
  assert.equal(session.processingStats.failedFiles, 0, 'no stale failure state after a tenant switch');

  const remove = storage.remove.bind(storage);
  storage.remove = async (key) => { if (key === 'pendingSessionConfig') calls.push('storage.remove:binding'); return remove(key); };
  calls.length = 0;
  await session.startStandalone({ dropOutbox: true });
  // Unbind live AND persisted before dropping the outbox, as the tenant-change path requires.
  assert.deepEqual(calls.slice(-2), ['storage.remove:binding', 'uploader.clearOutbox']);
  assert.ok(calls.indexOf('uploader.setConfig:null') < calls.indexOf('storage.remove:binding'));
}

async function test_restore_rehydrates_state_and_binding() {
  const storage = fakeStorage({
    pendingSessionConfig: { projectId: 'p9' },
    capturedFilesMeta: [{ url: 'https://acme.com/a.js', contentLength: 7 }, { url: 'https://acme.com/b.js', contentLength: 5 }],
    capturedObservations: [
      { method: 'GET', url: '/a' }, { method: 'GET', url: '/a' }, { method: 'POST', url: '/b' },
      { method: 'GET', url: '/c' }, { method: 'GET', url: '/d' }, { bad: true },
    ],
  });
  const { session, uploader } = makeSession({ storage, dedupEntries: [{ contentHash: 'h1', url: 'u1', capturedAt: 1 }, null] });
  await session.restore();
  assert.equal(session.sessionId, 's0');
  assert.equal(uploader.config.projectId, 'p9');
  assert.deepEqual([...session.capturedHashes.keys()], ['h1']);
  assert.equal(session.capturedFiles.size, 2);
  assert.equal(session.totalCapturedBytes, 12, 'byte cap survives a respawn');
  assert.deepEqual(session.observations.map((o) => `${o.method} ${o.url}`), ['GET /a', 'POST /b', 'GET /c'], 'deduped and capped');
}

async function test_captured_meta_projection_is_lean() {
  const { session } = makeSession();
  const lean = session.projectCapturedFile({
    url: 'u', content: 'x'.repeat(100), sourceMap: { mappings: '' }, contentLength: 100,
    hasSourceMap: true, secretCount: 1, dependencies: ['a', 'b'],
  });
  assert.equal(lean.content, undefined, 'raw JS never hits chrome.storage');
  assert.equal(lean.sourceMap, undefined);
  assert.equal(lean.dependencyCount, 2);
  assert.equal(lean.hasSourceMap, true);
}

async function test_observations_persist_without_bodies() {
  const storage = fakeStorage();
  const { session } = makeSession({ storage });
  session.observations.push({ method: 'POST', url: '/login', reqBody: '{"password":"[redacted]"}' });
  session.persistObservations();
  await Promise.resolve();
  assert.deepEqual(storage.data.capturedObservations, [{ method: 'POST', url: '/login' }], 'no request bodies on disk');
}

async function test_restore_keeps_a_live_capture_that_beat_it() {
  const storage = fakeStorage({ capturedFilesMeta: [{ url: 'u1', contentLength: 1, secretCount: 0 }] });
  const { session } = makeSession({ storage });
  session.capturedFiles.set('u1', { url: 'u1', contentLength: 1, secretCount: 9 });
  await session.restore();
  assert.equal(session.capturedFiles.get('u1').secretCount, 9, 'rehydrate only fills urls not already present');
}

async function test_every_transition_wipes_persisted_state() {
  for (const run of [
    (s) => s.startNew({ binding: { projectId: 'p1' } }),
    (s) => s.startStandalone(),
  ]) {
    const storage = fakeStorage({ capturedFilesMeta: [{ url: 'u' }], capturedObservations: [{ method: 'GET', url: '/x' }] });
    const { session } = makeSession({ storage });
    await session.restore();
    await run(session);
    assert.equal(storage.data.capturedFilesMeta, undefined);
    assert.equal(storage.data.capturedObservations, undefined);
  }
}

await test_start_new_flushes_then_rotates_and_binds();
await test_observations_persist_without_bodies();
await test_restore_keeps_a_live_capture_that_beat_it();
await test_every_transition_wipes_persisted_state();
await test_start_standalone_unbinds_and_optionally_drops_outbox();
await test_restore_rehydrates_state_and_binding();
await test_captured_meta_projection_is_lean();
console.log('ok - CaptureSession lifecycle and persistence');
