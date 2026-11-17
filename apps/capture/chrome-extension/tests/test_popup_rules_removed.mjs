// Q6 (ADR-0010): the popup has no rules editor. The denylist editor and default-profile switch, the
// out-of-scope Tag/Mute/Exclude choice and "Mute plugins & trackers" are gone, together with the worker
// code and the stored settings only they used; values an older build stored are removed at worker start.
// The default noise behaviour is the built-in third-party list plus the team's additions, no popup control.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { buildSettingsViewModel } from '../src/popup/viewmodels/settingsVm.js';
import { buildHomeViewModel } from '../src/popup/viewmodels/homeVm.js';
import { matchesThirdPartyFile } from '../modules/asset-classifier.js';
import { setTeamRules, resetTeamRules } from '../modules/team-rules.js';

const root = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const sources = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (name === 'node_modules' || name === 'dist' || name === 'tests') continue;
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.(js|jsx|html)$/.test(name)) sources.push([p, readFileSync(p, 'utf8')]);
  }
};
walk(root);

// 1) No source reads or writes the removed settings, except the one cleanup list that deletes them.
const REMOVED = ['denyRules', 'denyDefaultProfile', 'outOfScopeMode', 'muteNoise'];
for (const [file, text] of sources) {
  for (const key of REMOVED) {
    const hits = text.split('\n').filter((l) => l.includes(key) && !/^\s*(\/\/|\*)/.test(l));
    if (file.endsWith('background.js')) {
      // background.js may name them only inside the legacy cleanup list.
      assert.ok(hits.every((l) => /LEGACY_SETTING_KEYS|^\s*'/.test(l)), `${file} still uses ${key}: ${hits.join(' | ')}`);
    } else {
      assert.equal(hits.length, 0, `${file} still uses ${key}: ${hits.join(' | ')}`);
    }
  }
}
for (const gone of ['matchesDenylist', 'DEFAULT_DENY_RULES', 'TAG_COLOR', 'ruleTag', 'toggleDefaultProfile', 'setOutOfScopeMode']) {
  for (const [file, text] of sources) assert.ok(!text.includes(gone), `${file} still mentions ${gone}`);
}

// 2) Stored values from older builds are removed at worker start, before anything reads settings.
const bg = readFileSync(join(root, 'background.js'), 'utf8');
const list = bg.match(/LEGACY_SETTING_KEYS = \[([^\]]*)\]/);
assert.ok(list, 'a LEGACY_SETTING_KEYS list exists in background.js');
for (const key of REMOVED) assert.ok(list[1].includes(`'${key}'`), `${key} is cleaned up`);
const init = bg.slice(bg.indexOf('async initialize()'));
assert.ok(init.indexOf('LEGACY_SETTING_KEYS') < init.indexOf('this.loadSettings()') + 200
  || init.indexOf('LEGACY_SETTING_KEYS') < init.indexOf('loadSavedTeamRules'), 'cleanup runs at worker start');
assert.match(init.slice(0, 1500), /chrome\.storage\.local\.remove\(LEGACY_SETTING_KEYS\)/);

// 3) The popup's view-models carry no rules control.
const settingsVm = buildSettingsViewModel({
  closeSettings() {}, connState: '', latency: '', testConnection() {}, settings: {}, status: {},
  signOut() {}, patchSettings() {}, version: '0',
});
for (const key of ['denyRules', 'denyDefaultProfile', 'toggleDefaultProfile', 'outOfScopeMode', 'setOutOfScopeMode', 'newRule', 'addRule', 'removeRule']) {
  assert.ok(!(key in settingsVm), `settings view-model has no ${key}`);
}
assert.ok('captureEverything' in settingsVm && 'maxAssetMb' in settingsVm && 'captureResponseBodies' in settingsVm, 'Capture rules stay');
const homeVm = buildHomeViewModel({
  status: {}, elapsedMs: 0, delivery: null, health: null, connectionLabel: '', flow: {},
  analysis: {}, canAnalyze: false, projects: [], projectId: '', settings: {},
  reauth() {}, openSettings() {}, toggleSetting() {}, openWorkspace() {}, analyzeNow() {}, selectProject() {}, createProject() {},
});
assert.deepEqual(homeVm.toggles.map((t) => t.key), ['captureAuthContext'], 'no "Mute plugins & trackers" toggle');
const popupText = sources.filter(([f]) => f.includes('popup')).map(([, t]) => t).join('\n');
for (const label of ['Mute plugins', 'NOISE DENYLIST', 'Out-of-scope assets', 'Default profile']) {
  assert.ok(!popupText.includes(label), `the popup no longer says "${label}"`);
}

// 4) The default noise behaviour has no switch: built-in file patterns always apply, plus the team's.
resetTeamRules();
assert.equal(matchesThirdPartyFile('https://app.acme.io/static/jquery-3.6.0.min.js'), true);
assert.equal(matchesThirdPartyFile('https://acme.io/wp-content/plugins/p.js'), true);
assert.equal(matchesThirdPartyFile('https://x.doubleclick.net/ad.js'), true);
assert.equal(matchesThirdPartyFile('https://app.acme.io/main.js'), false);
setTeamRules({ hosts: [], filePatterns: ['/vendor/chat*.js'] });
assert.equal(matchesThirdPartyFile('https://app.acme.io/vendor/chat-widget.js'), true);
resetTeamRules();

console.log('ok - popup rules editor, Tag/Mute/Exclude and Mute toggle are gone');
