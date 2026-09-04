// D41 — an expired/rejected auth token (401/403) must NOT drop captured batches. They are
// re-queued and the drain is PAUSED (authPaused) until re-auth; resumeUploads() (on re-login) lifts
// the pause and drains the backlog. 400/422 stay permanent drops. clearOutbox() (tenant switch /
// new session) must also lift a latched pause (review R1). Mirrors test_s0/test_upload_timeout.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const source = fs.readFileSync(uploaderPath, 'utf8').replace('export class BatchUploader', 'class BatchUploader');

let nextResponse = { ok: true, json: async () => ({ success: true }) };
const sandbox = {
  console, URL, setTimeout, clearTimeout, AbortController,
  chrome: { notifications: { create: () => {} } },
  fetch: async () => nextResponse
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });
const BatchUploader = sandbox.BatchUploader;

const makeFile = (hash) => ({ url: `https://example.com/${hash}.js`, contentHash: hash, sessionId: 's1', contentLength: 5, content: 'x;' });
const fresh = () => { const u = new BatchUploader(); u.setEndpoint('http://localhost:3000'); return u; };
const okResp = { ok: true, json: async () => ({ success: true }) };

async function run() {
  // 1) 401 -> re-queued (NOT dropped), authPaused set, onAuthFailure fired with the status.
  {
    const u = fresh();
    let failedStatus = null;
    u.setOnAuthFailure((s) => { failedStatus = s; });
    nextResponse = { ok: false, status: 401, text: async () => 'expired' };
    u.pendingQueue.push(makeFile('a'), makeFile('b'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 2, '401 batch is re-queued, not dropped');
    assert.equal(u.stats.droppedFiles, 0, '401 must not drop files');
    assert.equal(u.authPaused, true, '401 pauses the drain');
    assert.equal(failedStatus, 401, 'onAuthFailure fires with the 401 status');
    assert.equal(u.getStats().authPaused, true, 'getStats exposes authPaused');

    // 2) While paused, processBatch is a no-op even with a now-healthy server.
    nextResponse = okResp;
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 2, 'processBatch does nothing while paused');
    assert.equal(u.stats.uploadedFiles, 0, 'nothing uploads while paused');

    // 3) resumeUploads() lifts the pause and drains under the healthy server.
    u.resumeUploads();
    await new Promise((r) => setTimeout(r, 20));       // let the 0ms drain timer fire
    while (u.isUploading) await new Promise((r) => setTimeout(r, 10));
    clearTimeout(u.uploadTimer);
    assert.equal(u.authPaused, false, 'resumeUploads clears the pause');
    assert.equal(u.pendingQueue.length, 0, 'the backlog drains after resume');
    assert.equal(u.stats.uploadedFiles, 2, 'the previously-paused batch uploads after resume');
  }

  // 4) 403 behaves like 401 (authExpired).
  {
    const u = fresh();
    nextResponse = { ok: false, status: 403, text: async () => 'forbidden' };
    u.pendingQueue.push(makeFile('c'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 1, '403 batch re-queued');
    assert.equal(u.authPaused, true, '403 pauses the drain');
    assert.equal(u.stats.droppedFiles, 0);
  }

  // 5) 422 still drops (permanent), does NOT pause.
  {
    const u = fresh();
    nextResponse = { ok: false, status: 422, text: async () => 'invalid' };
    u.pendingQueue.push(makeFile('d'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.pendingQueue.length, 0, '422 dropped');
    assert.equal(u.stats.droppedFiles, 1);
    assert.equal(u.authPaused, false, '422 does not pause');
  }

  // 6) clearOutbox() lifts a latched pause (R1: tenant switch / new session must not stay stuck).
  {
    const u = fresh();
    nextResponse = { ok: false, status: 401, text: async () => 'expired' };
    u.pendingQueue.push(makeFile('e'));
    await u.processBatch();
    clearTimeout(u.uploadTimer);
    assert.equal(u.authPaused, true);
    await u.clearOutbox();
    assert.equal(u.authPaused, false, 'clearOutbox lifts a latched auth-pause');
    assert.equal(u.pendingQueue.length, 0, 'clearOutbox empties the queue');
  }

  console.log('test_auth_expiry: ok');
  process.exit(0);
}
run().catch((e) => { console.error(e); process.exit(1); });
