// The popup's one-line capture state (PR 3a stand-in): plain words, never a run ID.
import assert from 'node:assert/strict';
import { captureLine } from '../src/popup/viewmodels/homeVm.js';

assert.equal(captureLine({}), null);
assert.equal(captureLine({ capture: { runId: 'run-1', scope: ['acme.io'], newSites: [] } }), 'Capturing · acme.io');
assert.equal(captureLine({ capture: { runId: 'run-1', scope: ['acme.io'], paused: true, newSites: ['beta.example'] } }),
  'Paused · acme.io · 1 new site seen');
assert.equal(captureLine({ capture: { runId: 'run-1', scope: ['acme.io'], ending: true, newSites: [] } }),
  'Ending… finishing uploads · acme.io');
assert.equal(captureLine({ lastEnded: { runId: 'run-1', scope: ['acme.io'] } }), 'Capture ended · acme.io');
assert.ok(!captureLine({ capture: { runId: 'run-1', scope: ['acme.io'], newSites: [] } }).includes('run-1'));
console.log('test_capture_line: ok');
