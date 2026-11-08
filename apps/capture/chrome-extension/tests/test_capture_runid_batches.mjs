// Capture lifecycle — every file carries its capture's runId (stamped at enqueue, persisted in the
// outbox), a batch never mixes captures, and a capture the server has ended closes quietly.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const source = fs.readFileSync(uploaderPath, 'utf8').replace('export class BatchUploader', 'class BatchUploader');

const sent = [];
let nextResponse = null;
const notifications = [];
const sandbox = {
  console, URL, setTimeout, clearTimeout, AbortController,
  chrome: { notifications: { create: (n) => notifications.push(n) } },
  fetch: async (_url, init) => {
    sent.push(JSON.parse(init.body));
    return nextResponse || { ok: true, json: async () => ({ success: true, paired: true }) };
  },
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });
const { BatchUploader } = sandbox;

function memStore() {
  const m = new Map();
  return {
    _m: m,
    async put(k, v) { m.set(k, v); },
    async delete(k) { m.delete(k); },
    async clear() { m.clear(); },
    async getAll() { return [...m.values()]; },
  };
}
const file = (hash, runId, sessionId = 's1') => ({
  url: `https://app.acme.io/${hash}.js`, contentHash: hash, sessionId, runId, contentLength: 2, content: 'x;',
});
function fresh(store) {
  const u = new BatchUploader({ apiEndpoint: 'http://x/api/save-files', batchSize: 5, batchInterval: 60000 });
  u.setStore(store);
  return u;
}

async function run() {
  // 1) The same file in two captures of one session keeps two outbox entries.
  {
    const store = memStore();
    const u = fresh(store);
    await u.enqueue(file('h', 'run-1'));
    await u.enqueue(file('h', 'run-2'));
    clearTimeout(u.uploadTimer);
    assert.equal(store._m.size, 2, 'runId is part of the outbox key');
    assert.ok([...store._m.keys()].every((k) => k.startsWith('s1:run-')), 'key is sid:runId:hash');
  }

  // 2) A batch is a leading run of files from ONE capture, and names that capture.
  {
    sent.length = 0;
    const u = fresh(memStore());
    for (const f of [file('a', 'run-1'), file('b', 'run-1'), file('c', 'run-2'), file('d', 'run-2')]) {
      await u.enqueue(f);
    }
    clearTimeout(u.uploadTimer);
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(sent.length, 2, 'two captures, two batches');
    assert.deepEqual(sent.map((b) => b.metadata.runId), ['run-1', 'run-2']);
    assert.deepEqual(sent.map((b) => b.files.map((f) => f.contentHash).join('')), ['ab', 'cd']);
  }

  // 3) capture_ended: dropped quietly (no "Upload Rejected" per batch), and the owner is told once.
  {
    sent.length = 0;
    notifications.length = 0;
    const store = memStore();
    const u = fresh(store);
    const ended = [];
    u.setOnCaptureEnded((runId) => ended.push(runId));
    nextResponse = { ok: false, status: 409, text: async () => JSON.stringify({ detail: 'capture_ended' }) };
    await u.enqueue(file('e', 'run-9'));
    clearTimeout(u.uploadTimer);
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    nextResponse = null;
    assert.deepEqual(ended, ['run-9']);
    assert.equal(notifications.length, 0, 'no rejection notification for an ended capture');
    assert.equal(u.pendingQueue.length, 0, 'not re-queued');
    assert.equal(store._m.size, 0, 'and gone from the durable outbox');
  }

  // 4) A file from before the capture lifecycle (no runId) keeps the old key and path.
  {
    sent.length = 0;
    const store = memStore();
    const u = fresh(store);
    await u.enqueue(file('z', undefined));
    clearTimeout(u.uploadTimer);
    assert.deepEqual([...store._m.keys()], ['s1:z']);
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(sent[0].metadata.runId, undefined);
  }

  console.log('test_capture_runid_batches: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
