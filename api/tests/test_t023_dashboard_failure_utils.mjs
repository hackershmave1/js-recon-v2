import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const utilsPath = path.resolve(__dirname, '../app/static/dashboard-failure-utils.js');
const source = fs.readFileSync(utilsPath, 'utf8');

const sandbox = { console, globalThis: {} };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: utilsPath });

const utils = sandbox.globalThis.DashboardFailureUtils;
assert.ok(utils, 'DashboardFailureUtils should be attached to global object');

const analysisFailure = utils.deriveFileFailure({
  analysisStatus: 'failed',
  analysisError: 'Extractor crashed while parsing payload',
  sourceMap: null
});
assert.equal(analysisFailure.source, 'analysis');
assert.equal(analysisFailure.label, 'Analysis');

const sourcemapFailure = utils.deriveFileFailure({
  analysisStatus: 'failed',
  analysisError: 'Source map processing failed: HTTP error 404',
  sourceMap: { processingStatus: 'failed', processingError: 'HTTP error 404' }
});
assert.equal(sourcemapFailure.source, 'sourcemap');
assert.equal(sourcemapFailure.label, 'Source map');

const fetchFailure = utils.deriveFileFailure({
  analysisStatus: 'failed',
  analysisError: 'Fetch timeout while loading script',
  sourceMap: null
});
assert.equal(fetchFailure.source, 'capture_fetch');
assert.equal(fetchFailure.label, 'Capture/fetch');

const noFailure = utils.deriveFileFailure({
  analysisStatus: 'completed',
  analysisError: null,
  sourceMap: { processingStatus: 'completed' }
});
assert.equal(noFailure, null);

console.log('test_t023_dashboard_failure_utils: ok');
