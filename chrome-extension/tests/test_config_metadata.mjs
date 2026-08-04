// BatchUploader.setConfig / configMetadata — the project binding + non-scope config snapshot
// is stamped onto each save-files upload. Fetch-capture pattern (like test_t007).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const transformed = fs.readFileSync(uploaderPath, 'utf8').replace('export class BatchUploader', 'class BatchUploader');

const captured = [];
const sandbox = {
  console, URL, setTimeout, clearTimeout, AbortController,
  chrome: { notifications: { create: () => {} } },
  fetch: async (_url, init) => { captured.push(JSON.parse(init.body)); return { ok: true, json: async () => ({ success: true }) }; },
};
vm.createContext(sandbox);
vm.runInContext(`${transformed}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });
const BatchUploader = sandbox.BatchUploader;

const files = [{ url: 'https://a.com/x.js', contentHash: 'h', sessionId: 's', contentLength: 3, content: 'a=1' }];

async function run() {
  const uploader = new BatchUploader();
  uploader.setEndpoint('http://localhost:3000');

  // With a config set, the payload metadata carries projectId/captureConfig/overrideKeys.
  uploader.setConfig({
    projectId: 'proj-1',
    captureConfig: { capture: { outOfScopeMode: 'exclude', maxAssetMb: 5 }, denylist: { rules: [], useDefaultProfile: true }, analysis: { analyzeOnUpload: false, captureSourceMaps: true } },
    overrideKeys: ['capture.outOfScopeMode'],
  });
  await uploader.upload(files);
  const m1 = captured[0].metadata;
  assert.equal(m1.projectId, 'proj-1');
  assert.equal(m1.captureConfig.capture.outOfScopeMode, 'exclude');
  assert.deepEqual(m1.overrideKeys, ['capture.outOfScopeMode']);

  // Standalone: null projectId is omitted, but the captureConfig snapshot is still carried.
  uploader.setConfig({ projectId: null, captureConfig: { analysis: { analyzeOnUpload: true } }, overrideKeys: [] });
  await uploader.upload(files);
  const m2 = captured[1].metadata;
  assert.equal('projectId' in m2, false, 'null projectId omitted');
  assert.equal(m2.captureConfig.analysis.analyzeOnUpload, true);
  assert.deepEqual(m2.overrideKeys, []);

  // No config -> none of the three keys appear (back-compat with today's payload).
  uploader.setConfig(null);
  await uploader.upload(files);
  const m3 = captured[2].metadata;
  assert.equal('projectId' in m3, false);
  assert.equal('captureConfig' in m3, false);
  assert.equal('overrideKeys' in m3, false);

  console.log('test_config_metadata: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
