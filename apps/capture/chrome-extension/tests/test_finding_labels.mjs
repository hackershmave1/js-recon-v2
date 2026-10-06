// Unit tests for modules/finding-labels.js: the popup must name finding types with the same
// words as the web workspace (apps/platform/web/src/api/findingLabels.ts). The sync test reads
// that file's TYPE_LABELS block, so drift between the two maps fails here instead of reaching
// an operator as two names for one finding.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { TYPE_LABELS, typeLabel } from '../modules/finding-labels.js';

function test_unresolved_lane_reads_as_the_workspace_label() {
  // The QA case: "Top: endpoint_unresolved" -> "Top: suspected call".
  assert.equal(typeLabel('endpoint_unresolved'), 'suspected call');
  assert.equal(typeLabel('endpoint_suspected'), 'inferred API');
  assert.equal(typeLabel('endpoint'), 'API');
}

function test_unlabelled_types_fall_back_to_the_wire_token() {
  assert.equal(typeLabel('secret'), 'secret');
  assert.equal(typeLabel('param'), 'param');
  assert.equal(typeLabel(undefined), '');
}

function test_labels_match_the_workspace_map() {
  const tsPath = fileURLToPath(
    new URL('../../../platform/web/src/api/findingLabels.ts', import.meta.url)
  );
  const src = readFileSync(tsPath, 'utf8');
  const block = src.slice(src.indexOf('TYPE_LABELS'), src.indexOf('};', src.indexOf('TYPE_LABELS')));
  const workspace = {};
  for (const m of block.matchAll(/^\s*(\w+):\s*"([^"]+)"/gm)) workspace[m[1]] = m[2];
  assert.ok(Object.keys(workspace).length >= 8, 'parsed the workspace TYPE_LABELS block');
  assert.deepEqual(TYPE_LABELS, workspace, 'popup labels must equal the workspace labels');
}

const tests = [
  test_unresolved_lane_reads_as_the_workspace_label,
  test_unlabelled_types_fall_back_to_the_wire_token,
  test_labels_match_the_workspace_map
];

let passed = 0;
for (const t of tests) { t(); passed += 1; }
console.log(`finding-labels: ${passed}/${tests.length} passed`);
