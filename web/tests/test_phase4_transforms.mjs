// Phase-4 transform tests (UI-002): activity VM, search, export serialization.
// Run: node tests/test_phase4_transforms.mjs
import assert from 'node:assert/strict';
import {
  jobActivityVm, searchFindings, findingsToJson, findingsToCsv
} from '../src/transforms.overlays.js';
import { findingsFromAnalysis } from '../src/transforms.js';

// --- jobActivityVm: stepper + progress derived from coverage/status ---
const running = jobActivityVm({
  jobId: 'j1', status: 'running', targets: ['https://wishandwash.co.il'],
  coverage: { discovered_js: 10, fetched_js: 10, analyzed_js: 4, rates: { fetchPct: 100, analysisPct: 40 } }
});
assert.equal(running.active, true);
assert.equal(running.doneState, false);
assert.equal(running.canStop, true);
assert.equal(running.stageLabel, 'Analyzing JS');
assert.equal(running.done, 4);
assert.equal(running.total, 10);
assert.equal(running.pct, 40);
assert.equal(running.title, 'wishandwash.co.il');
// stages: Discover/Fetch done, Analyze active, Done pending
assert.equal(running.stages[0].state, 'done');
assert.equal(running.stages[1].state, 'done');
assert.equal(running.stages[2].state, 'active');
assert.equal(running.stages[3].state, 'pending');

// fetching (no analysis yet) → fetch rate + fetch label
const fetching = jobActivityVm({
  status: 'running', targets: ['https://x.io'],
  coverage: { discovered_js: 8, fetched_js: 3, analyzed_js: 0, rates: { fetchPct: 38 } }
});
assert.equal(fetching.stageLabel, 'Fetching assets');
assert.equal(fetching.pct, 38);
assert.equal(fetching.stages[2].state, 'pending');

// queued job with nothing yet → Discover active
const queued = jobActivityVm({ status: 'queued', targets: ['https://q.io'], coverage: {} });
assert.equal(queued.active, true);
assert.equal(queued.stages[0].state, 'active');
assert.equal(queued.stageLabel, 'Discovering');

// completed → terminal, all stages done, summary line, no stop
const done = jobActivityVm({
  status: 'completed', targets: ['https://d.io'],
  coverage: { discovered_js: 5, fetched_js: 5, analyzed_js: 5, map_fetched: 2 },
  summary: { stored: 5 }
});
assert.equal(done.active, false);
assert.equal(done.doneState, true);
assert.equal(done.canStop, false);
assert.equal(done.stages.every((s) => s.state === 'done'), true);
assert.match(done.summary, /5 files stored/);
assert.match(done.summary, /2 maps/);

// failed → carries error in summary
const failed = jobActivityVm({ status: 'failed', targets: ['https://f.io'], error: 'boom', coverage: {} });
assert.equal(failed.doneState, true);
assert.match(failed.summary, /Failed: boom/);

// --- search + export over real-shaped findings ---
const analysis = {
  secrets: [
    { value: 'AKIA0000000000000000', ruleName: 'AWS Access Key', file: 'https://wishandwash.co.il/app.js', line: 11, confidence: 'high' }
  ],
  endpoints: [
    { url: '/api/internal/admin/users', method: 'POST', file: 'https://wishandwash.co.il/app.js', line: 88, confidence: 'high' },
    { url: '/api/flags', method: 'GET', file: 'https://wishandwash.co.il/lib.js', line: 9, confidence: 'medium' }
  ]
};
const fs = findingsFromAnalysis(analysis, 'wishandwash.co.il');

// search: substring over label/value/file, ranked
const r = searchFindings(fs, 'admin');
assert.equal(r.length, 1);
assert.equal(r[0].finding.value.includes('admin'), true);
assert.equal(r[0].type.label, 'ENDPOINT');
assert.ok(r[0].fileLine.includes(':88'));
assert.equal(searchFindings(fs, '').length, 0);
assert.equal(searchFindings(fs, 'zzznotfound').length, 0);
// "api" matches both endpoints; limit respected
assert.equal(searchFindings(fs, 'api', 1).length, 1);

// export JSON: parseable, count + selected columns present
const json = JSON.parse(findingsToJson(fs, { target: 'wishandwash.co.il' }));
assert.equal(json.count, 3);
assert.equal(json.target, 'wishandwash.co.il');
assert.equal(json.findings.length, 3);
assert.ok('fingerprint' in json.findings[0]);
assert.ok('value' in json.findings[0]);

// export CSV: header + one row per finding, quoting of commas
const csv = findingsToCsv(fs).split('\n');
assert.equal(csv.length, 1 + 3);
assert.equal(csv[0].split(',')[0], 'kind');
const withComma = findingsToCsv([{ kind: 'secret', value: 'a,b', label: 'x"y' }]);
assert.ok(withComma.includes('"a,b"'));
assert.ok(withComma.includes('"x""y"'));

console.log('phase4 transforms: all assertions passed');
