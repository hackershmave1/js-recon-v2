// The popup's capture screens, as pure view-model helpers: which screen shows, how a scope reads,
// what "Continue" offers, and the words for each state. Never a run ID.
import assert from 'node:assert/strict';
import {
  capturePhase, suggestScope, typedScope, scopeLabel, formatElapsed, continueOptions,
  defaultChoice, liveSubtitle, endedBanner, startErrorText, analyzeLabel,
} from '../src/popup/viewmodels/captureVm.js';

// --- which screen ---
assert.equal(capturePhase({}), 'idle');
assert.equal(capturePhase({ capture: { runId: 'r', scope: ['acme.io'] } }), 'live');
assert.equal(capturePhase({ capture: { runId: 'r', scope: ['acme.io'], paused: true } }), 'paused');
assert.equal(capturePhase({ capture: { runId: 'r', scope: ['acme.io'], paused: true, ending: true } }), 'ending');

// --- one-click scope from the tab ---
assert.deepEqual(suggestScope('app.acme.io'), { host: 'app.acme.io', scope: 'acme.io', subdomains: true });
assert.deepEqual(suggestScope('foo.github.io'), { host: 'foo.github.io', scope: 'foo.github.io', subdomains: true });
assert.deepEqual(suggestScope('127.0.0.1'), { host: '127.0.0.1', scope: '127.0.0.1', subdomains: false });
assert.equal(suggestScope(''), null, 'a tab that is not a website offers nothing');

// --- a typed scope is normalized like every other scope write, and kept as typed ---
assert.equal(typedScope('https://App.Acme.io/login'), 'app.acme.io');
assert.equal(typedScope('*.acme.io'), 'acme.io');
assert.equal(typedScope('   '), '');

assert.equal(scopeLabel('acme.io'), 'acme.io + subdomains');
assert.equal(scopeLabel('localhost'), 'localhost');
assert.equal(scopeLabel(['acme.io', 'beta.example']), 'acme.io + subdomains, beta.example + subdomains');

// --- the timer ---
assert.equal(formatElapsed(0), '0:00');
assert.equal(formatElapsed(42_000), '0:42');
assert.equal(formatElapsed(372_000), '6:12');
assert.equal(formatElapsed(3_723_000), '1:02:03');

// --- Continue options: one per matching session, labelled with its engagement ---
const sessions = [
  {
    external_id: 'ext-1', engagement_id: 'eng-1', captures: 4, last_capture_at: '2026-11-05T07:35:00Z',
    largest: { run_id: 'run-9', files: 211, endpoints: 544 },
  },
  { external_id: 'ext-2', engagement_id: null, captures: 1, last_capture_at: null, largest: null },
];
const projects = [{ id: 'eng-1', name: 'Acme Q4 pentest' }];
const options = continueOptions(sessions, projects, 'acme.io');
assert.equal(options.length, 2);
assert.equal(options[0].key, 'ext-1');
assert.equal(options[0].title, 'Continue “acme.io”');
assert.equal(options[0].engagement, 'Acme Q4 pentest');
assert.equal(options[0].line, '4 captures · last Nov 5');
assert.equal(options[0].best, 'largest: 211 files · 544 endpoints');
assert.equal(options[1].engagement, 'Solo · standalone');
assert.equal(options[1].line, '1 capture');
assert.equal(options[1].best, '');
assert.ok(!JSON.stringify(options.map(({ match, ...shown }) => shown)).includes('run-9'), 'no run ID is shown');
// A session whose engagement the popup can't name (deleted, not loaded) still says it has one.
assert.equal(continueOptions([{ external_id: 'x', engagement_id: 'gone', captures: 0 }], [], 'acme.io')[0].engagement, 'Another engagement');
assert.equal(continueOptions([{ engagement_id: null }], [], 'acme.io').length, 0, 'a match with no external id cannot be continued');

// Continue is pre-selected when a session exists; otherwise New.
assert.equal(defaultChoice(options), 'ext-1');
assert.equal(defaultChoice([]), 'new');

// --- the live header ---
assert.equal(
  liveSubtitle({ mode: 'continue', ordinal: 5, scope: ['acme.io'], elapsedMs: 42_000 }, 152),
  'Continuing acme.io · capture 5 · 0:42 · 152 files');
assert.equal(liveSubtitle({ mode: 'new', scope: ['acme.io'], elapsedMs: 3_000 }, 1), 'New session · 0:03 · 1 file');
assert.equal(
  liveSubtitle({ mode: 'continue', ordinal: 5, scope: ['acme.io'], elapsedMs: 260_000, paused: true, pausedByRestart: true }, 140),
  'Continuing acme.io · capture 5 · paused by restart · 4:20 · 140 files');
// A capture started by an older worker carries no mode: say nothing rather than guess.
assert.equal(liveSubtitle({ scope: ['acme.io'], elapsedMs: 1000 }, 0), '0:01 · 0 files');

// --- the "Capture ended" banner ---
assert.equal(endedBanner(null), null);
assert.deepEqual(endedBanner({ scope: ['acme.io'], reason: 'ended' }), {
  title: 'Capture ended · acme.io',
  body: 'The final analysis finishes in the workspace. The scope is cleared for your next capture.',
});
assert.match(endedBanner({ scope: ['acme.io'], reason: 'ended_elsewhere' }).body, /ended by the workspace/i);

// --- Start failures read as what to do next ---
assert.equal(startErrorText({ error: 'ending' }), 'The previous capture is still finishing — try again in a moment');
assert.equal(startErrorText({ error: 'session_expired' }), 'Session expired — sign in to start');
assert.equal(startErrorText({ error: 'HTTP 500' }), 'Could not start the capture');

// --- Analyze: it never ends the capture, and says when it is updating itself ---
assert.equal(analyzeLabel({ status: 'idle' }, 152), 'Analyze 152 scripts');
assert.equal(analyzeLabel({ status: 'idle' }, 1), 'Analyze 1 script');
assert.equal(analyzeLabel({ status: 'starting' }, 5), 'Starting analysis…');
assert.equal(analyzeLabel({ status: 'running', counts: { completed: 64, total: 152 } }, 152), 'Analyzing… 64/152');
assert.equal(analyzeLabel({ status: 'running', updating: true, counts: { completed: 1, total: 3 } }, 155), 'Updating with new files… 1/3');
assert.equal(analyzeLabel({ status: 'done', counts: { completed: 152 } }, 152), 'Analyzed ✓ · 152 done');

console.log('test_capture_vm: ok');
