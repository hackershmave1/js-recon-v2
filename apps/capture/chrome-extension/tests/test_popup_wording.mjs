// Popup wording and starting states the researcher walkthrough (#177) found wrong. Source-pinned
// (the popup is JSX, bundled by esbuild), so each assertion names the friction it guards.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const app = read('src/popup/app.jsx');
const login = read('src/popup/components/LoginView.jsx');
const settings = read('src/popup/components/SettingsView.jsx');
const picker = read('src/popup/components/EngagementPicker.jsx');

// F03: "Connected" is a result, not a starting value.
assert.match(app, /useState\('idle'\)/, 'the connection state starts idle');
assert.doesNotMatch(app, /const \[connState, setConnState\] = useState\('ok'\)/);
for (const [name, src] of [['LoginView', login], ['SettingsView', settings]]) {
  assert.match(src, /idle: \{ label: 'Not tested'/, `${name} has a Not tested state`);
  assert.match(src, /CONN\[vm\.connState\] \|\| CONN\.idle/, `${name} falls back to idle, never to Connected`);
}
assert.match(app, /setConnState\('ok'\);\s*\/\/ the workspace just answered a sign-in/, 'a successful sign-in is a connection result');

// F02: the first-run help describes scope-first, with the engagement optional.
assert.doesNotMatch(login, /Pick an engagement/);
assert.match(login, /Use the current site, or type the target.s domain\. An engagement is optional\./);

// F05: one word for one thing.
assert.doesNotMatch(app, /Project created|Project name required|create project/);
assert.match(app, /Engagement created ·/);

// F06: no scope is sent with a new engagement, and the picker no longer hands one over.
assert.doesNotMatch(app, /rootDomains: String\(rootDomains/);
assert.match(app, /api\.createProject\(body\)/);
assert.doesNotMatch(picker, /createProject\(newName, scope\)/);

console.log('test_popup_wording: ok');
