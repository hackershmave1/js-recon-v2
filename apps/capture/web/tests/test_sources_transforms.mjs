// Sources-view transform tests (UI-002 Phase 3). Run: node tests/test_sources_transforms.mjs
import assert from 'node:assert/strict';
import {
  isJsBundle, hasReconstructed, bundleLabel, sortedSourceRows,
  findingsForDoc, findingsByLine, findingsFromAnalysis
} from '../src/transforms.js';

// --- isJsBundle: by content-type or URL extension ---
assert.equal(isJsBundle({ contentType: 'application/javascript', url: 'https://x/a' }), true);
assert.equal(isJsBundle({ contentType: 'text/html', url: 'https://x/app.js' }), true);
assert.equal(isJsBundle({ contentType: 'text/html', url: 'https://x/page' }), false);
assert.equal(isJsBundle({ contentType: '', url: 'https://x/m.mjs?v=1' }), true);

// --- hasReconstructed: needs completed status + count > 0 ---
assert.equal(hasReconstructed({ sourceMap: { processingStatus: 'completed', reconstructedFilesCount: 3 } }), true);
assert.equal(hasReconstructed({ sourceMap: { processingStatus: 'completed_limited', reconstructedFilesCount: 1 } }), true);
assert.equal(hasReconstructed({ sourceMap: { processingStatus: 'completed', reconstructedFilesCount: 0 } }), false);
assert.equal(hasReconstructed({ sourceMap: { processingStatus: 'failed', reconstructedFilesCount: 5 } }), false);
assert.equal(hasReconstructed({ sourceMap: null }), false);
assert.equal(hasReconstructed({}), false);

// --- bundleLabel: last path segment, host fallback ---
assert.equal(bundleLabel('https://wishandwash.co.il/assets/index-4f2a.js'), 'index-4f2a.js');
assert.equal(bundleLabel('https://wishandwash.co.il/'), 'wishandwash.co.il');
assert.equal(bundleLabel('not a url/foo/bar.js'), 'bar.js');

// --- sortedSourceRows: path-sorted, depth = folders deep, basename name ---
const rows = sortedSourceRows([
  { path: 'src/utils/dom.js', size: 10, type: 'javascript' },
  { path: 'index.js', size: 5, type: 'javascript' }
]);
assert.equal(rows[0].path, 'index.js');
assert.equal(rows[0].depth, 0);
assert.equal(rows[0].name, 'index.js');
assert.equal(rows[1].path, 'src/utils/dom.js');
assert.equal(rows[1].depth, 2);
assert.equal(rows[1].name, 'dom.js');

// --- findingsForDoc: bundle scope + raw path equality (no normalization) ---
const findings = [
  { fileId: 'b1', file: 'src/a.js', line: 3 },
  { fileId: 'b1', file: 'src/b.js', line: 7 },
  { fileId: 'b2', file: 'src/a.js', line: 9 }
];
assert.equal(findingsForDoc(findings, 'b1', null).length, 2);   // whole bundle
assert.equal(findingsForDoc(findings, 'b1', 'src/a.js').length, 1);
assert.equal(findingsForDoc(findings, 'b2', 'src/a.js').length, 1);
assert.equal(findingsForDoc(findings, 'b1', 'missing.js').length, 0);

// --- findingsByLine: groups by line number ---
const byLine = findingsByLine(findingsForDoc(findings, 'b1', null));
assert.deepEqual(Object.keys(byLine).sort(), ['3', '7']);
assert.equal(byLine[3].length, 1);

// --- findingsFromAnalysis carries source_file_id as fileId ---
const built = findingsFromAnalysis({
  secrets: [{ value: 'sk_live_x', source_file_id: 'bundle-9', file: 'src/secret.js', line: 12, confidence: 'high' }],
  endpoints: []
}, 'wishandwash.co.il');
assert.equal(built.length, 1);
assert.equal(built[0].fileId, 'bundle-9');
assert.equal(built[0].file, 'src/secret.js');
assert.equal(built[0].line, 12);

console.log('OK — all Sources transform tests passed');
