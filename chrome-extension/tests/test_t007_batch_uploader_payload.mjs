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

uploader.setPerformAnalysisOnUpload(true);
await uploader.upload(testFiles);
assert.equal(capturedPayloads[1].metadata.performAnalysis, true);

console.log('test_t007_batch_uploader_payload: ok');
