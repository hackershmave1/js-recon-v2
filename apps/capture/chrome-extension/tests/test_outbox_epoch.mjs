// Tenant-isolation epoch guard (batch-uploader). A batch already in flight when clearOutbox()
// runs (a tenant switch) must be DROPPED on a transient failure, not re-queued — otherwise a
// previous tenant's files resurface in the queue and flush under the NEW tenant's token. This is
// the robust half of the cross-tenant-leak fix (reordering the login alone can't close the
// in-flight-retry path). Runtime test — batch-uploader is a class module (vm-loaded).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const source = fs.readFileSync(uploaderPath, 'utf8').replace('export class BatchUploader', 'class BatchUploader');

let uploader;
// A fetch that clears the outbox MID-FLIGHT (simulating a tenant switch that lands while this
// batch is uploading) and then returns a transient 500 so upload() throws a retriable error.
const fetchImpl = async () => {
  await uploader.clearOutbox();
  return { ok: false, status: 500, json: async () => ({ error: 'server error' }) };
};

const sandbox = {
  console, URL, setTimeout, clearTimeout, AbortController,
  chrome: { notifications: { create: () => {} } },
  fetch: fetchImpl
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });

async function run() {
  uploader = new sandbox.BatchUploader();
  uploader.setEndpoint('http://localhost:3000');
  // One file queued under epoch 0.
  uploader.pendingQueue.push({ url: 'https://a.example/app.js', contentHash: 'h1', sessionId: 's1', contentLength: 5, content: 'x=1;' });
  assert.equal(uploader.epoch, 0, 'starts at epoch 0');

  await uploader.processBatch();

  // The in-flight batch failed transiently, but clearOutbox() bumped the epoch during the upload,
  // so it must be DROPPED — never re-queued (which would leak under the new tenant's token).
  assert.equal(uploader.epoch, 1, 'clearOutbox bumped the epoch mid-flight');
  assert.equal(uploader.pendingQueue.length, 0, 'stale-epoch batch is NOT re-queued');
  assert.ok(uploader.getStats().droppedFiles >= 1, 'stale-epoch batch counted as dropped');

  // Control: a normal transient failure (no epoch bump) DOES re-queue for retry. The module reads
  // `fetch` from the sandbox global at call time, so reassigning it here is enough.
  sandbox.fetch = async () => ({ ok: false, status: 500, json: async () => ({ error: 'server error' }) });
  const u2 = new sandbox.BatchUploader();
  u2.setEndpoint('http://localhost:3000');
  u2.pendingQueue.push({ url: 'https://a.example/b.js', contentHash: 'h2', sessionId: 's1', contentLength: 5, content: 'y=2;' });
  await u2.processBatch();
  assert.equal(u2.pendingQueue.length, 1, 'a normal transient failure (no epoch bump) re-queues for retry');

  console.log('test_outbox_epoch: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
