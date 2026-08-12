// Slice 3 — the operator-pairing Bearer token is attached to every tenant-resolving
// ingest call (save-files + analyze/start + analyze/progress + GET/POST projects) and
// NOT to the tenant-agnostic /health probe. Also: the token is whitespace-normalized
// (a wrapped-paste newline in a header value makes fetch throw), an empty token adds no
// header (shared-tenant fallback = today's behavior), and the uploader records the
// `paired` ack so the popup can reflect pairing state.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function loadClass(relPath, exportName) {
  const modPath = path.resolve(__dirname, relPath);
  const source = fs.readFileSync(modPath, 'utf8').replace(`export class ${exportName}`, `class ${exportName}`);
  return { modPath, source };
}

// --------------------------------------------------------------------------- //
// BatchUploader — Authorization: Bearer on POST /api/save-files
// --------------------------------------------------------------------------- //
async function testUploaderBearer() {
  const { modPath, source } = loadClass('../modules/batch-uploader.js', 'BatchUploader');
  const captured = [];
  let nextBody = { success: true };
  const ctl = { midFlight: null };  // lets a test mutate state DURING an in-flight request
  const sandbox = {
    console, URL, setTimeout, clearTimeout, AbortController,
    chrome: { notifications: { create: () => {} } },
    fetch: async (_url, init) => {
      captured.push(init);
      if (ctl.midFlight) ctl.midFlight();
      return { ok: true, json: async () => nextBody };
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: modPath });
  const uploader = new sandbox.BatchUploader();
  uploader.setEndpoint('http://localhost:8000');
  const files = [{ url: 'https://ex.com/a.js', contentHash: 'h1', sessionId: 's1', content: 'x' }];

  // 1. No token => no Authorization header (today's unauthenticated ingest).
  await uploader.upload(files);
  assert.equal(captured.at(-1).headers.Authorization, undefined, 'no token => no Authorization header');

  // 2. A token => exact Bearer header.
  uploader.setAuthToken('tok-abc.123');
  await uploader.upload(files);
  assert.equal(captured.at(-1).headers.Authorization, 'Bearer tok-abc.123', 'token => Bearer header');

  // 3. Whitespace (incl. an interior newline from a wrapped paste) is stripped — else
  //    fetch throws on an invalid header value and the batch retries forever.
  uploader.setAuthToken('  tok-abc\n.123 \t');
  await uploader.upload(files);
  assert.equal(captured.at(-1).headers.Authorization, 'Bearer tok-abc.123', 'all whitespace stripped from token');

  // 4. Clearing the token drops the header again (back to shared-tenant fallback).
  uploader.setAuthToken('');
  await uploader.upload(files);
  assert.equal(captured.at(-1).headers.Authorization, undefined, 'empty token clears the header');

  // 5. The save-files `paired` ack is recorded and surfaced via getStats (drives the popup).
  assert.equal(uploader.getStats().paired, null, 'paired is null before a paired ack (fresh token cleared it)');
  uploader.setAuthToken('tok');
  nextBody = { success: true, paired: true };
  await uploader.upload(files);
  assert.equal(uploader.getStats().paired, true, 'paired:true ack recorded');
  nextBody = { success: true, paired: false };
  await uploader.upload(files);
  assert.equal(uploader.getStats().paired, false, 'paired:false ack recorded');

  // 6. Changing the token re-arms verification (clears a stale paired verdict).
  uploader.setAuthToken('a-different-token');
  assert.equal(uploader.getStats().paired, null, 'token change resets paired to null');

  // 7. Control chars (tab/newline/NUL/DEL) are stripped too — the bytes that make fetch
  //    throw on a header value. Built via fromCharCode so no literal control byte in source.
  uploader.setAuthToken('t' + String.fromCharCode(9, 10, 0, 127) + 'ok.sig');
  await uploader.upload(files);
  assert.equal(captured.at(-1).headers.Authorization, 'Bearer tok.sig', 'control chars stripped from token');

  // 8. F1 race: a late ack from a PRIOR token must not stamp `paired` onto a token the
  //    operator changed to while the request was in flight (else a new, unverified token
  //    inherits the old one's ✓). Snapshot-at-send gates the record.
  uploader.setAuthToken('token-A');
  nextBody = { success: true, paired: true };
  ctl.midFlight = () => { uploader.setAuthToken('token-B'); };
  await uploader.upload(files);
  ctl.midFlight = null;
  assert.equal(uploader.getStats().paired, null, 'late ack from token-A is not recorded for token-B');

  console.log('  uploader Bearer: ok');
}

// --------------------------------------------------------------------------- //
// WorkspaceClient — Authorization on the 4 non-ingest tenant-resolving calls, NOT /health
// --------------------------------------------------------------------------- //
async function testWorkspaceClientAuth() {
  const { modPath, source } = loadClass('../modules/workspace-client.js', 'WorkspaceClient');
  const calls = [];
  const sandbox = {
    console, URL, setTimeout, clearTimeout, AbortController, Date, Promise, encodeURIComponent,
    fetch: async (url, init) => {
      calls.push({ url, init });
      return { ok: true, status: 200, json: async () => ({}) };
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
  const WorkspaceClient = sandbox.WorkspaceClient;

  const make = (pairingToken) => new WorkspaceClient({
    getSettings: () => ({ workspaceUrl: 'http://localhost:8000', pairingToken }),
    getSessionId: () => 'sess-1'
  });
  const authOf = (needle) => calls.find((c) => c.url.includes(needle))?.init?.headers?.Authorization;
  const ctOf = (needle) => calls.find((c) => c.url.includes(needle))?.init?.headers?.['Content-Type'];
  const reset = () => { calls.length = 0; };

  // With a token: all 4 tenant-resolving calls carry the Bearer; /health does NOT.
  const wc = make('  tok-1\n23 ');
  reset(); await wc.analyzeSession();        assert.equal(authOf('/analyze/start'), 'Bearer tok-123', 'analyze/start carries Bearer');
  reset(); await wc.getAnalysisProgress();   assert.equal(authOf('/analyze/progress'), 'Bearer tok-123', 'analyze/progress carries Bearer');
  reset(); await wc.listProjects();          assert.equal(authOf('/api/projects'), 'Bearer tok-123', 'GET projects carries Bearer');
  reset(); await wc.createProject({ name: 'x' }); assert.equal(authOf('/api/projects'), 'Bearer tok-123', 'POST projects carries Bearer');
  reset(); await wc.testConnection();        assert.equal(authOf('/api/health'), undefined, '/health must stay unauthenticated');

  // The Bearer spread must NOT clobber Content-Type on the two POSTs.
  reset(); await wc.analyzeSession();        assert.equal(ctOf('/analyze/start'), 'application/json', 'analyze/start keeps Content-Type through the spread');
  reset(); await wc.createProject({ name: 'x' }); assert.equal(ctOf('/api/projects'), 'application/json', 'POST projects keeps Content-Type through the spread');

  // Without a token: no Authorization on ANY of the 4 (today's shared-tenant behavior).
  const wc0 = make('');
  reset(); await wc0.analyzeSession();        assert.equal(authOf('/analyze/start'), undefined, 'no token => no Bearer on analyze/start');
  reset(); await wc0.getAnalysisProgress();   assert.equal(authOf('/analyze/progress'), undefined, 'no token => no Bearer on analyze/progress');
  reset(); await wc0.listProjects();          assert.equal(authOf('/api/projects'), undefined, 'no token => no Bearer on GET projects');
  reset(); await wc0.createProject({ name: 'x' }); assert.equal(authOf('/api/projects'), undefined, 'no token => no Bearer on POST projects');

  console.log('  workspace-client auth: ok');
}

async function run() {
  await testUploaderBearer();
  await testWorkspaceClientAuth();
  console.log('test_pairing_bearer: ok');
  process.exit(0);
}
run().catch((e) => { console.error(e); process.exit(1); });
