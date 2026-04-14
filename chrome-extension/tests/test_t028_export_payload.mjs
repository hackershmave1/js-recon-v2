import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const modulePath = path.resolve(__dirname, '../modules/export-builder.js');
const source = fs.readFileSync(modulePath, 'utf8');
const transformed = source
  .replace(/export function /g, 'function ');

const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(`${transformed}\nthis.buildExportData = buildExportData;`, sandbox, { filename: modulePath });

const { buildExportData } = sandbox;

const files = [
  {
    url: 'https://example.com/app.js',
    contentHash: 'hash-1',
    sessionId: 'session-1',
    contentLength: 123,
    content: 'console.log("hello");',
    hasSourceMap: true,
    repPlusSummary: { importedHintCount: 2 }
  }
];

const metadataOnly = buildExportData({
  sessionId: 'session-1',
  files,
  includeContent: false,
  exportDate: '2026-02-09T00:00:00.000Z'
});
assert.equal(metadataOnly.metadata.includeContent, false);
assert.equal(metadataOnly.metadata.totalFiles, 1);
assert.equal(metadataOnly.files[0].url, files[0].url);
assert.equal(metadataOnly.files[0].repPlusSummary.importedHintCount, 2);
assert.equal(Object.prototype.hasOwnProperty.call(metadataOnly.files[0], 'content'), false);

const fullExport = buildExportData({
  sessionId: 'session-1',
  files,
  includeContent: true,
  exportDate: '2026-02-09T00:00:00.000Z'
});
assert.equal(fullExport.metadata.includeContent, true);
assert.equal(fullExport.files[0].content, files[0].content);

console.log('test_t028_export_payload: ok');
