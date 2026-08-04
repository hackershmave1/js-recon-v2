// S0 — size-cap alignment + non-retriable upload handling.
//
// Behavioral guard for the bug the adversarial gate surfaced: a 4xx rejection
// used to be unshift-ed back onto the queue and retried forever. Also pins the
// client per-file cap <= the backend's 10 MB limit and the new manifest perms.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// --- static invariants (client cap must never exceed the server cap) ---
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
assert.match(bg, /maxFileBytes:\s*10\s*\*\s*1024\s*\*\s*1024/, 'client maxFileBytes must be 10 MB (matches backend MAX_JS_CONTENT_SIZE)');
assert.ok(!/maxFileBytes:\s*50\s*\*/.test(bg), 'stale 50 MB client cap must be gone');
const mani = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../manifest.json'), 'utf8'));
assert.ok(mani.permissions.includes('alarms'), 'alarms permission required for the durable flush timer');
assert.ok(mani.permissions.includes('unlimitedStorage'), 'unlimitedStorage required for the durable outbox');

// --- load BatchUploader in a sandbox with a controllable fetch ---
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

function fresh() {
  const u = new BatchUploader();
  u.setEndpoint('http://localhost:3000');
  return u;
}

async function run() {
  // 1) Non-retriable 422 -> batch DROPPED, not re-queued (the fix).
  {
    const u = fresh();
    nextResponse = { ok: false, status: 422, text: async () => 'invalid' };
    u.pendingQueue.push(makeFile('a'), makeFile('b'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 0, '422 batch must be dropped, not re-queued');
    assert.equal(u.stats.droppedFiles, 2, 'droppedFiles counts the dropped batch');
    assert.equal(u.stats.failedBatches, 1);
  }

  // 2) Retriable 500 -> batch re-queued for a later retry.
  {
    const u = fresh();
    nextResponse = { ok: false, status: 500, text: async () => 'server error' };
    u.pendingQueue.push(makeFile('c'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 1, '500 batch must be re-queued (transient)');
    assert.equal(u.stats.droppedFiles, 0, '500 must not drop files');
  }

  // 3) Retriable 429 (rate limit) -> re-queued, not dropped.
  {
    const u = fresh();
    nextResponse = { ok: false, status: 429, text: async () => 'too many' };
    u.pendingQueue.push(makeFile('d'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 1, '429 must be re-queued (retriable)');
    assert.equal(u.stats.droppedFiles, 0);
  }

  // 4) Success -> uploaded, queue drained.
  {
    const u = fresh();
    nextResponse = { ok: true, json: async () => ({ success: true }) };
    u.pendingQueue.push(makeFile('e'), makeFile('f'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 0);
    assert.equal(u.stats.uploadedFiles, 2);
  }

  console.log('test_s0_upload_retry_and_caps: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
