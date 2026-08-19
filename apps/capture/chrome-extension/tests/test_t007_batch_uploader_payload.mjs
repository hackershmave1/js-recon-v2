import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const uploaderPath = path.resolve(__dirname, '../modules/batch-uploader.js');
const source = fs.readFileSync(uploaderPath, 'utf8');
const transformed = source.replace('export class BatchUploader', 'class BatchUploader');

const capturedPayloads = [];
const sandbox = {
  console,
  URL,
  setTimeout,
  clearTimeout,
  AbortController,
  chrome: {
    notifications: {
      create: () => {}
    }
  },
  fetch: async (_url, init) => {
    capturedPayloads.push(JSON.parse(init.body));
    return {
      ok: true,
      json: async () => ({ success: true })
    };
  }
};

vm.createContext(sandbox);
vm.runInContext(`${transformed}\nthis.BatchUploader = BatchUploader;`, sandbox, { filename: uploaderPath });

const BatchUploader = sandbox.BatchUploader;
const uploader = new BatchUploader();
uploader.setEndpoint('http://localhost:3000');

const testFiles = [
  {
    url: 'https://example.com/app.js',
    contentHash: 'hash123',
    sessionId: 'session-1',
    contentLength: 10,
    content: 'console.log(1);'
  }
];

uploader.setPerformAnalysisOnUpload(false);
await uploader.upload(testFiles);
assert.equal(capturedPayloads[0].metadata.performAnalysis, false);
// Decouple (S3): analyze-on-upload OFF => disableAnalysis TRUE (true fast store).
assert.equal(capturedPayloads[0].metadata.disableAnalysis, true);

uploader.setPerformAnalysisOnUpload(true);
await uploader.upload(testFiles);
assert.equal(capturedPayloads[1].metadata.performAnalysis, true);
assert.equal(capturedPayloads[1].metadata.disableAnalysis, false);

// getStats() surfaces the bound engagement projectId (drives the popup's Active-engagement
// display) — null when Standalone, the id when bound. Mirrors configMetadata()'s source.
assert.equal(uploader.getStats().projectId, null, 'no config => standalone (projectId null)');
uploader.setConfig({ projectId: 'eng-1' });
assert.equal(uploader.getStats().projectId, 'eng-1', 'setConfig surfaces projectId via getStats');
uploader.setConfig(null);
assert.equal(uploader.getStats().projectId, null, 'setConfig(null) reverts to standalone');

// clearOutbox() drops all pending work (tenant-isolation guard on a tenant switch).
uploader.pendingQueue.push({ url: 'https://x/y.js', contentHash: 'h', sessionId: 's' });
assert.ok(uploader.getStats().pendingQueueLength > 0, 'has pending work before clear');
await uploader.clearOutbox();
assert.equal(uploader.getStats().pendingQueueLength, 0, 'clearOutbox empties the pending queue');

console.log('test_t007_batch_uploader_payload: ok');
