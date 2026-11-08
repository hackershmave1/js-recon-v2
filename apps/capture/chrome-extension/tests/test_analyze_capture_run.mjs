// Capture lifecycle — "Analyze now" during a capture names the capture (runId), is NOT blocked by
// files still uploading (the platform re-analyzes late files itself), still refuses on an expired
// sign-in, passes "up to date" through, and asks progress for THIS capture.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class WorkspaceClient', 'class WorkspaceClient');

const requests = [];
let reply = { started: true, message: 'analysis started', runId: 'run-1' };
const sandbox = {
  console, setTimeout, clearTimeout, AbortController,
  fetch: async (url, init) => {
    requests.push({ url, body: init && init.body ? JSON.parse(init.body) : null });
    return { ok: true, json: async () => reply };
  },
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
const { WorkspaceClient } = sandbox;

const client = (stats, runId = 'run-1') => new WorkspaceClient({
  getSettings: () => ({ workspaceUrl: 'http://localhost:8000' }),
  getSessionId: () => 'sess-1',
  getRunId: () => runId,
  batchUploader: { flushAll: async () => {}, getStats: () => stats },
});

async function run() {
  // Files still uploading: analyze proceeds, naming the capture.
  requests.length = 0;
  let res = await client({ pendingQueueLength: 4, isUploading: true, authPaused: false }).analyzeSession();
  assert.equal(res.success, true, 'live uploads do not block Analyze during a capture');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.runId, 'run-1');
  assert.equal(res.runId, 'run-1');

  // Expired sign-in: still refused (it would never drain).
  requests.length = 0;
  res = await client({ pendingQueueLength: 2, isUploading: false, authPaused: true }).analyzeSession();
  assert.equal(res.reason, 'session_expired');
  assert.equal(requests.length, 0);

  // "Up to date" passes through.
  reply = { started: false, upToDate: true, message: 'analysis is up to date', runId: 'run-1' };
  res = await client({ pendingQueueLength: 0, isUploading: false, authPaused: false }).analyzeSession();
  assert.equal(res.upToDate, true);
  assert.equal(res.started, false);

  // Progress asks for this capture and reports which run it describes.
  requests.length = 0;
  reply = { success: true, runId: 'run-1', job: { counts: { total: 3 }, files: [] } };
  res = await client({}).getAnalysisProgress();
  assert.match(requests[0].url, /analyze\/progress\?runId=run-1$/);
  assert.equal(res.runId, 'run-1');

  // No capture (legacy flow): no runId sent, progress unscoped.
  requests.length = 0;
  await client({}, null).getAnalysisProgress();
  assert.match(requests[0].url, /analyze\/progress$/);

  console.log('test_analyze_capture_run: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
