// D43c — analyzeSession must NOT start analysis while captures are still pending OR a batch is
// mid-flight; otherwise analysis runs on a partial set yet reports "complete ✓". It also
// differentiates an expired session (never drains without re-auth) from merely-slow uploads.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class WorkspaceClient', 'class WorkspaceClient');

let fetchCalls = 0;
const sandbox = {
  console, setTimeout, clearTimeout, AbortController,
  fetch: async () => { fetchCalls += 1; return { ok: true, json: async () => ({ started: true }) }; }
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
const WorkspaceClient = sandbox.WorkspaceClient;

// A stub uploader: flushAll is a no-op; getStats returns the queue state under test.
const makeUploader = (stats) => ({ flushAll: async () => {}, getStats: () => stats });
const makeClient = (uploader) => new WorkspaceClient({
  getSettings: () => ({ workspaceUrl: 'http://localhost:8000' }),
  getSessionId: () => 'sess-1',
  batchUploader: uploader
});

async function run() {
  // 1) Files still queued -> blocked (pending_uploads); analyze/start never POSTed.
  fetchCalls = 0;
  let res = await makeClient(makeUploader({ pendingQueueLength: 3, isUploading: false, authPaused: false })).analyzeSession();
  assert.equal(res.success, false, 'pending>0 blocks analyze');
  assert.equal(res.reason, 'pending_uploads');
  assert.equal(res.pending, 3);
  assert.equal(fetchCalls, 0, 'analyze/start must not POST on a partial set');

  // 2) Queue drained to 0 but a batch is mid-flight -> still blocked (the isUploading edge, R2).
  fetchCalls = 0;
  res = await makeClient(makeUploader({ pendingQueueLength: 0, isUploading: true, authPaused: false })).analyzeSession();
  assert.equal(res.success, false, 'a mid-flight batch (pending 0, isUploading true) still blocks');
  assert.equal(res.reason, 'pending_uploads');
  assert.equal(fetchCalls, 0);

  // 3) Expired session (authPaused) with pending files -> distinct reason for the UI (R3).
  fetchCalls = 0;
  res = await makeClient(makeUploader({ pendingQueueLength: 2, isUploading: false, authPaused: true })).analyzeSession();
  assert.equal(res.reason, 'session_expired', 'authPaused surfaces as session_expired, not pending_uploads');
  assert.equal(fetchCalls, 0);

  // 4) Fully drained -> analyze proceeds (POST fired once, success).
  fetchCalls = 0;
  res = await makeClient(makeUploader({ pendingQueueLength: 0, isUploading: false, authPaused: false })).analyzeSession();
  assert.equal(res.success, true, 'a fully-drained outbox proceeds to analyze');
  assert.equal(fetchCalls, 1, 'analyze/start POSTed exactly once');

  console.log('test_analyze_blocks_on_pending: ok');
  process.exit(0);
}
run().catch((e) => { console.error(e); process.exit(1); });
