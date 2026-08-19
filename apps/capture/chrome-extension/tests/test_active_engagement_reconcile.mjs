// Unit tests for the pure active-engagement helpers (modules/active-engagement.js). Pure module
// (no chrome/fetch/DOM), so imported directly — no vm/export-stripping needed.
import assert from 'node:assert/strict';
import { reconcileActiveProject, activeProjectName } from '../modules/active-engagement.js';

const projects = [
  { id: 'eng-1', name: 'Starbucks' },
  { id: 'eng-2', name: 'Attio' },
];

function test_reconcile_keeps_a_real_id() {
  assert.equal(reconcileActiveProject('eng-2', projects), 'eng-2', 'a present id is kept');
}

function test_reconcile_drops_a_stale_id() {
  // Deleted engagement, or a different tenant after re-login: the id is not in the list.
  assert.equal(reconcileActiveProject('eng-gone', projects), null, 'an absent id falls back to Standalone');
}

function test_reconcile_standalone_and_empties() {
  assert.equal(reconcileActiveProject(null, projects), null, 'null id => Standalone');
  assert.equal(reconcileActiveProject('', projects), null, 'empty id => Standalone');
  assert.equal(reconcileActiveProject('eng-1', []), null, 'empty list => Standalone');
  assert.equal(reconcileActiveProject('eng-1', null), null, 'missing list => Standalone (never throws)');
}

function test_active_project_name() {
  assert.equal(activeProjectName('eng-1', projects), 'Starbucks', 'resolves the name from the list');
  assert.equal(activeProjectName('eng-gone', projects), '', 'unknown id => empty (render Solo)');
  assert.equal(activeProjectName(null, projects), '', 'Standalone => empty');
  assert.equal(activeProjectName('eng-1', null), '', 'missing list => empty (never throws)');
}

test_reconcile_keeps_a_real_id();
test_reconcile_drops_a_stale_id();
test_reconcile_standalone_and_empties();
test_active_project_name();
console.log('test_active_engagement_reconcile: ok');
