// Session-rotation races (DEBT D69 + D70, issue #267). The platform binds a session to one
// engagement immutably, so a capture filed under the wrong (session, engagement) pair can't be
// moved afterwards.
//
// D69: a worker respawn in the middle of a transition must never restore the NEW session id
//      with the OLD engagement binding. Simulated by snapshotting chrome.storage after every
//      write the transition makes, then cold-starting a fresh CaptureSession from each snapshot.
// D70: a batch already taken off the processing queue keeps the session id it was taken under;
//      once a rotation replaced that id, the rest of the batch is not filed into the new session.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { CaptureSession } from '../modules/capture-session.js';
import { SessionStore } from '../modules/session-store.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// chrome.storage.local stand-in that records the persisted state after every write, i.e. every
// point where the worker could be torn down mid-transition.
function crashPointStorage(initial = {}) {
  const data = structuredClone(initial);
  const snapshots = [];
  const snap = () => snapshots.push(structuredClone(data));
  return {
    data,
    snapshots,
    async get(key) { return key in data ? { [key]: structuredClone(data[key]) } : {}; },
    async set(obj) { Object.assign(data, structuredClone(obj)); snap(); },
    async remove(key) { delete data[key]; snap(); },
  };
}

function plainStorage(initial = {}) {
  const data = structuredClone(initial);
  return {
    data,
    async get(key) { return key in data ? { [key]: structuredClone(data[key]) } : {}; },
    async set(obj) { Object.assign(data, structuredClone(obj)); },
    async remove(key) { delete data[key]; },
  };
}

function makeSession(storage) {
  const uploader = {
    config: undefined,
    async flushAll() {},
    setConfig(config) { this.config = config; },
    async clearOutbox() {},
  };
  const session = new CaptureSession({
    storage,
    sessionStore: new SessionStore(storage),
    dedupStore: { async getAll() { return []; }, async clear() {} },
    uploader,
    authTracker: { clear() {} },
    getSettings: () => ({}),
    observationCap: 10,
  });
  return { session, uploader };
}

// Cold-start a fresh worker from one persisted snapshot: what id and binding does it come back with?
async function respawnFrom(snapshot) {
  const { session, uploader } = makeSession(plainStorage(snapshot));
  await session.restore();
  return { sessionId: session.sessionId, binding: uploader.config ?? null };
}

const OLD_BINDING = { projectId: 'eng-old', captureConfig: {}, overrideKeys: [] };
const NEW_BINDING = { projectId: 'eng-new', captureConfig: {}, overrideKeys: [] };
const SEED = { reconSessionId: 'sess-old', pendingSessionConfig: OLD_BINDING };

// Every crash point during a transition restores either the whole old state or the whole new one.
async function assertNoMixedRespawn(name, transition, expectNew) {
  const storage = crashPointStorage(SEED);
  const { session } = makeSession(storage);
  await session.restore();
  storage.snapshots.length = 0;
  await transition(session);
  const newId = session.sessionId;
  assert.notEqual(newId, 'sess-old', `${name}: the transition switched session`);
  assert.ok(storage.snapshots.length > 0, `${name}: the transition persisted something`);
  for (const [i, snapshot] of storage.snapshots.entries()) {
    const restored = await respawnFrom(snapshot);
    const where = `${name}: respawn after write #${i + 1}`;
    if (restored.sessionId === newId) {
      assert.deepEqual(restored.binding, expectNew, `${where} restored the new id with the wrong binding`);
    } else {
      assert.equal(restored.sessionId, 'sess-old', `${where} restored an unexpected id`);
      assert.deepEqual(restored.binding, OLD_BINDING, `${where} lost the old session's binding`);
    }
  }
  const final = await respawnFrom(storage.snapshots.at(-1));
  assert.equal(final.sessionId, newId, `${name}: a respawn after the transition resumes the new id`);
  assert.deepEqual(final.binding, expectNew, `${name}: ... with the new binding`);
}

async function test_d69_new_session_respawn_never_pairs_new_id_with_old_engagement() {
  await assertNoMixedRespawn('startNew', (s) => s.startNew({ binding: NEW_BINDING }), NEW_BINDING);
}

async function test_d69_adopt_respawn_never_pairs_adopted_id_with_old_engagement() {
  await assertNoMixedRespawn('adopt', (s) => s.adopt('sess-adopted', { binding: NEW_BINDING }), NEW_BINDING);
}

async function test_d69_standalone_respawn_never_rebinds_the_old_engagement() {
  // Logout / tenant change: the fresh session must come back unbound, never under the old tenant's engagement.
  await assertNoMixedRespawn('startStandalone', (s) => s.startStandalone({ dropOutbox: true }), null);
}

async function test_d69_live_binding_switches_with_the_id() {
  // In memory too: no await between the id switch and the binding switch, so an upload that
  // runs while the transition is suspended never reads the new id with the old binding.
  const storage = plainStorage(SEED);
  const { session, uploader } = makeSession(storage);
  await session.restore();
  const seen = [];
  const set = storage.set.bind(storage);
  storage.set = async (obj) => { await set(obj); await null; seen.push([session.sessionId, uploader.config?.projectId]); };
  await session.startNew({ binding: NEW_BINDING });
  seen.push([session.sessionId, uploader.config?.projectId]);
  for (const [id, projectId] of seen) {
    if (id !== 'sess-old') assert.equal(projectId, 'eng-new', 'the new id is never live with the old binding');
  }
}

// processQueue is the real method from background.js (the worker can't load in Node), run against a
// real CaptureSession; processFile is a stand-in that records what it was handed.
function loadProcessQueue() {
  const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
  const start = bg.indexOf('  async processQueue() {');
  const end = bg.indexOf('  async processFile(item) {');
  assert.ok(start > 0 && end > start, 'processQueue precedes processFile in background.js');
  return new Function(`return ({\n${bg.slice(start, end)}\n});`)().processQueue;
}

async function test_d70_dequeued_batch_keeps_its_session_and_is_not_filed_after_rotation() {
  const storage = plainStorage(SEED);
  const { session } = makeSession(storage);
  await session.restore();
  for (const n of [1, 2, 3]) session.processingQueue.push({ metadata: { url: `https://app.acme.io/${n}.js` } });

  let releaseFirst;
  const firstInFlight = new Promise((resolve) => { releaseFirst = resolve; });
  const handed = [];
  const worker = {
    session,
    processQueue: loadProcessQueue(),
    recordProcessingFailure() {},
    async processFile(item) {
      handed.push({ url: item.metadata.url, sessionId: item.sessionId });
      if (handed.length === 1) await firstInFlight;
    },
  };

  const drained = worker.processQueue();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(session.processingQueue.length, 0, 'the whole batch was taken off the queue');
  await session.startNew({ binding: NEW_BINDING });   // the operator clicks New session mid-batch
  releaseFirst();
  await drained;

  assert.equal(handed[0].sessionId, 'sess-old', 'a dequeued item carries the session id it was taken under');
  assert.deepEqual(handed.map((h) => h.url), ['https://app.acme.io/1.js'],
    'the rest of the batch is not processed into the new session');
}

async function test_d70_stale_dependency_does_not_drop_the_current_items_behind_it() {
  // A dependency an in-flight file queued after the rotation sits at the head of the NEW session's
  // batch: it is skipped, and the new session's own items behind it are still processed.
  const { session } = makeSession(plainStorage(SEED));
  await session.restore();
  await session.startNew({ binding: NEW_BINDING });
  session.processingQueue.push(
    { metadata: { url: 'https://app.acme.io/stale-dep.js' }, sessionId: 'sess-old' },
    { metadata: { url: 'https://app.acme.io/fresh.js' } },
  );
  const handed = [];
  const worker = {
    session,
    processQueue: loadProcessQueue(),
    recordProcessingFailure() {},
    async processFile(item) { handed.push({ url: item.metadata.url, sessionId: item.sessionId }); },
  };
  await worker.processQueue();
  assert.deepEqual(handed, [{ url: 'https://app.acme.io/fresh.js', sessionId: session.sessionId }]);
}

function test_d70_queued_dependency_keeps_its_parents_session() {
  const { session } = makeSession(plainStorage(SEED));
  session.sessionId = 'sess-new';
  session.processingQueue.push({ metadata: { url: 'dep.js' }, sessionId: 'sess-old' }, { metadata: { url: 'fresh.js' } });
  const batch = session.takeBatch(10);
  assert.deepEqual(batch.map((i) => i.sessionId), ['sess-old', 'sess-new']);
  assert.equal(session.isCurrent('sess-old'), false);
  assert.equal(session.isCurrent('sess-new'), true);
}

function test_d70_process_file_files_under_the_items_session() {
  // processFile can't run in Node; pin that it stamps the item's id, re-checks it after its last
  // await before filing, and that a dependency inherits its parent's id.
  const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
  const body = bg.slice(bg.indexOf('  async processFile(item) {'));
  const guard = body.search(/if \(!this\.session\.isCurrent\(item\.sessionId\)\) return;/);
  const lastAwaitBefore = body.lastIndexOf('await this.calculateHash(content)');
  const fileSet = body.indexOf('this.session.capturedFiles.set(url, fileObject)');
  assert.ok(guard > lastAwaitBefore && lastAwaitBefore > 0, 'the session check follows the last await before filing');
  assert.ok(fileSet > guard, 'the session check precedes filing the capture');
  assert.doesNotMatch(body.slice(guard, fileSet), /\bawait\b/, 'no await between the check and filing');
  const enqueue = body.indexOf('await this.batchUploader.enqueue(fileObject)');
  const recheck = body.indexOf('if (!this.session.isCurrent(item.sessionId)) return;', enqueue);
  const dedupPut = body.indexOf('this.dedupStore.put(contentHash');
  assert.ok(enqueue > fileSet && recheck > enqueue && dedupPut > recheck,
    'a rotation during enqueue stops the dedup entry, counters and dependencies leaking into the new session');
  assert.match(body, /sessionId: item\.sessionId,\s*\n\s*tabId: tabId/, 'the file is stamped with the item\'s session id');
  assert.match(body, /runId: item\.runId,[^\n]*\n\s*sessionId: item\.sessionId/, 'a dependency inherits its parent\'s session id');
}

await test_d69_new_session_respawn_never_pairs_new_id_with_old_engagement();
await test_d69_adopt_respawn_never_pairs_adopted_id_with_old_engagement();
await test_d69_standalone_respawn_never_rebinds_the_old_engagement();
await test_d69_live_binding_switches_with_the_id();
await test_d70_dequeued_batch_keeps_its_session_and_is_not_filed_after_rotation();
await test_d70_stale_dependency_does_not_drop_the_current_items_behind_it();
test_d70_queued_dependency_keeps_its_parents_session();
test_d70_process_file_files_under_the_items_session();
console.log('ok - session rotation races (D69, D70)');
