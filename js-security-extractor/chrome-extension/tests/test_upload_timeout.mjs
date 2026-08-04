// Review #H1 — a blackholed workspace must not hang upload() forever. The per-batch
// timeout aborts the fetch and surfaces a retriable error (batch re-queued, not dropped).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const source = fs.readFileSync(uploaderPath, 'utf8').replace('export class BatchUploader', 'class BatchUploader');

const sandbox = {
  console,
  URL,
  setTimeout,
  clearTimeout,
  AbortController,
  chrome: { notifications: { create: () => {} } },
  // A fetch that never resolves on its own, but honors abort (simulates a hung server).
  fetch: (_url, init) => new Promise((_resolve, reject) => {
    const signal = init && init.signal;
    if (signal) {
      const onAbort = () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); };
      if (signal.aborted) onAbort();
      else signal.addEventListener('abort', onAbort);
    }
  })
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });
const BatchUploader = sandbox.BatchUploader;
const makeFile = (h) => ({ url: `https://example.com/${h}.js`, contentHash: h, sessionId: 's1', contentLength: 5, content: 'x;' });

async function run() {
  // 1) upload() aborts promptly and throws a RETRIABLE error.
  const u = new BatchUploader();
  u.setEndpoint('http://localhost:3000');
  u.uploadTimeoutMs = 50;
  const start = Date.now();
  let threw = null;
  try { await u.upload([makeFile('a')]); } catch (e) { threw = e; }
  assert.ok(threw, 'upload rejects when the server hangs');
  assert.equal(threw.retriable, true, 'a timeout is retriable');
  assert.ok(Date.now() - start < 2000, 'upload aborts promptly instead of hanging');

  // 2) processBatch re-queues a timed-out batch (transient), does not drop it.
  const u2 = new BatchUploader();
  u2.setEndpoint('http://localhost:3000');
  u2.uploadTimeoutMs = 50;
  u2.pendingQueue.push(makeFile('b'));
  await u2.processBatch();
  clearTimeout(u2.uploadTimer);
  assert.equal(u2.pendingQueue.length, 1, 'timed-out batch is re-queued for retry');
  assert.equal(u2.stats.droppedFiles, 0, 'timeout must not drop files');

  console.log('test_upload_timeout: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
