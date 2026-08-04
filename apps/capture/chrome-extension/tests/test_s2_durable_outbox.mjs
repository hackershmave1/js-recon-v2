// S2 — durable upload outbox that survives service-worker teardown.
// Uses an in-memory store adapter (same shape as IdbStore) so no real IndexedDB
// is needed under node.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const source = fs.readFileSync(uploaderPath, 'utf8').replace('export class BatchUploader', 'class BatchUploader');

let nextResponse = { ok: true, json: async () => ({ success: true }) };
const sandbox = {
  console,
  URL,
  setTimeout,
  clearTimeout,
  AbortController,
  chrome: { notifications: { create: () => {} } },
  fetch: async () => nextResponse
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });
const BatchUploader = sandbox.BatchUploader;

const makeFile = (hash) => ({ url: `https://example.com/${hash}.js`, contentHash: hash, sessionId: 's1', contentLength: 5, content: 'x;' });

function memStore(seed = {}) {
  const m = new Map(Object.entries(seed));
  return {
    _m: m,
    async put(k, v) { m.set(k, v); },
    async delete(k) { m.delete(k); },
    async clear() { m.clear(); },
    async getAll() { return [...m.values()]; }
  };
}

function fresh(store) {
  const u = new BatchUploader();
  u.setEndpoint('http://localhost:3000');
  if (store) u.setStore(store);
  return u;
}

async function run() {
  // 1) enqueue persists; a successful upload forgets it and fires onDrained.
  {
    const store = memStore();
    const u = fresh(store);
    let drained = 0;
    u.setOnDrained(() => { drained += 1; });
    nextResponse = { ok: true, json: async () => ({ success: true }) };
    await u.enqueue(makeFile('a'));
    assert.equal(store._m.size, 1, 'enqueue persists to the outbox');
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(store._m.size, 0, 'successful upload removes it from the outbox');
    assert.equal(u.pendingQueue.length, 0);
    assert.ok(drained >= 1, 'onDrained fires when the queue empties');
  }

  // 2) A transient (500) failure keeps the file persisted for a later resume.
  {
    const store = memStore();
    const u = fresh(store);
    nextResponse = { ok: false, status: 500, text: async () => 'err' };
    await u.enqueue(makeFile('b'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(store._m.size, 1, '500 keeps the file in the outbox for resume');
    assert.equal(u.pendingQueue.length, 1);
  }

  // 3) A permanent (422) rejection forgets it (never resurrected on next boot).
  {
    const store = memStore();
    const u = fresh(store);
    nextResponse = { ok: false, status: 422, text: async () => 'bad' };
    await u.enqueue(makeFile('c'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(store._m.size, 0, '422 removes the file from the outbox');
    assert.equal(u.pendingQueue.length, 0);
  }

  // 4) rehydrate() restores files a dead worker left behind.
  {
    const store = memStore({ d: makeFile('d'), e: makeFile('e') });
    const u = fresh(store);
    const pending = await u.rehydrate();
    clearTimeout(u.uploadTimer);
    assert.equal(pending, 2, 'rehydrate reports the restored count');
    const restored = u.pendingQueue.map((f) => f.contentHash).sort().join(',');
    assert.equal(restored, 'd,e', `rehydrate restored the persisted files (got: ${restored})`);
  }

  // 5) rehydrate() does not double-add a file already in the live queue.
  {
    const store = memStore();
    const u = fresh(store);
    nextResponse = { ok: true, json: async () => ({ success: true }) };
    await u.enqueue(makeFile('f'));
    clearTimeout(u.uploadTimer);
    await u.rehydrate();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 1, 'rehydrate is idempotent against the live queue');
  }

  // 6) Cross-session: the SAME content under two session ids must NOT overwrite in the
  // outbox (regression for the contentHash-only key that lost old-session files).
  {
    const store = memStore();
    const u = fresh(store);
    const fileA = { ...makeFile('x'), sessionId: 'A' };
    const fileB = { ...makeFile('x'), sessionId: 'B' }; // same contentHash, different session
    await u.enqueue(fileA);
    await u.enqueue(fileB);
    clearTimeout(u.uploadTimer);
    assert.equal(store._m.size, 2, 'same hash across two sessions keeps two outbox entries');

    const u2 = fresh(store); // simulate a respawn
    await u2.rehydrate();
    clearTimeout(u2.uploadTimer);
    const sids = u2.pendingQueue.map((f) => f.sessionId).sort().join(',');
    assert.equal(sids, 'A,B', `both session copies survive a respawn (got: ${sids})`);
  }

  console.log('test_s2_durable_outbox: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
