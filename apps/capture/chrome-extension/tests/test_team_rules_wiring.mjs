// Source-level wiring of the team's third-party rules in the worker (ADR-0010, R2): saved copy at
// start, download at sign-in and capture start, forgotten on logout / tenant change, and the
// "Ignore for all my sessions" action reaching the lifecycle. Behaviour is covered in
// test_team_rules.mjs and test_capture_quarantine_decisions.mjs; this pins that it is connected.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const bg = readFileSync(new URL('../background.js', import.meta.url), 'utf8');
// The worker loads unbundled, so a syntax slip in background.js leaves a dead worker that no other
// source-text test notices (found in the R2 real run): parse it as a module.
const parsed = spawnSync(process.execPath, ['--check', '--input-type=module'], { input: bg, encoding: 'utf8' });
assert.equal(parsed.status, 0, `background.js does not parse: ${parsed.stderr}`);

const body = (name) => {
  const start = bg.indexOf(name);
  assert.ok(start > 0, `${name} exists in background.js`);
  return bg.slice(start, start + 6000);
};

assert.match(body('async initialize()'), /await loadSavedTeamRules\(chrome\.storage\.local\)/, 'the saved copy loads at start');
assert.match(body('captureStart: async'), /this\.refreshTeamRules\(\)/, 'a capture start downloads the list');
assert.doesNotMatch(body('captureStart: async'), /await this\.refreshTeamRules/, 'and never waits on it');
assert.match(body('async login(request'), /this\.refreshTeamRules\(\)/, 'sign-in downloads the list');
assert.match(body('async logout('), /await this\.forgetTeamRules\(\)/, 'logout forgets the previous tenant\'s list');
assert.match(body('async login(request'), /forgetTeamRules/, 'a tenant change forgets it too');
assert.match(body('captureIgnoreForAll'), /this\.capture\.ignoreForAll\(req\.site\)/);
assert.match(bg, /teamRulesChanged: \(\) => this\.refreshTeamRules\(\)/, 'adding a team host re-downloads the list');
assert.match(body('refreshTeamRules() {'), /authToken/, 'signed out: no download');

const popup = readFileSync(new URL('../src/popup/components/LiveCaptureCard.jsx', import.meta.url), 'utf8');
assert.match(popup, /Ignore for all my sessions/);

console.log('ok - team rules are wired into the worker and the popup');
