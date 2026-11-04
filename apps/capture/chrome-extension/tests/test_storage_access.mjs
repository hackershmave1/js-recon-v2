// storage.local holds the Bearer token + workspaceUrl and is readable/writable from content scripts
// by default (developer.chrome.com/docs/extensions/reference/api/storage, setAccessLevel). The
// worker closes it to them on every start and publishes ONLY the capture bit to storage.session.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { publishCaptureState, restrictStorageAccess } from '../modules/storage-access.js';

function area() {
  const data = {};
  return {
    data,
    level: null,
    async setAccessLevel({ accessLevel }) { this.level = accessLevel; },
    async set(items) { Object.assign(data, items); },
  };
}
const storage = { local: area(), session: area() };
await restrictStorageAccess(storage);
assert.equal(storage.local.level, 'TRUSTED_CONTEXTS', 'token/settings store closed to content scripts');
assert.equal(storage.session.level, 'TRUSTED_AND_UNTRUSTED_CONTEXTS', 'session store readable by the content script');

await publishCaptureState(storage, true);
assert.deepEqual(storage.session.data, { isCapturing: true });
await publishCaptureState(storage, 'yes'); // only a real boolean true counts
assert.deepEqual(storage.session.data, { isCapturing: false });

// Wiring: every worker start restricts first; capture-state writes and init publish the bit; the
// worker wakes on browser start/install so the lockdown precedes the first page load.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
const initAt = bg.indexOf('async initialize()');
const init = bg.slice(initAt, initAt + 400);
assert.ok(init.indexOf('restrictStorageAccess(chrome.storage)') >= 0, 'initialize restricts storage');
assert.ok(init.indexOf('restrictStorageAccess(chrome.storage)') < init.indexOf('this.loadSettings()'), 'restrict before anything else');
assert.match(bg, /persistCaptureState\(isCapturing\) \{[\s\S]*?publishCaptureState\(chrome\.storage, isCapturing\)/, 'capture toggles publish the bit');
assert.match(bg, /this\.isCapturing = this\.settings\.isCapturing \|\| false;\s*\/\/[^\n]*\n\s*await publishCaptureState/, 'init re-publishes after a browser restart');
assert.match(bg, /onStartup\.addListener/, 'wakes on browser start');
assert.match(bg, /onInstalled\.addListener/, 'wakes on install/update');

console.log('test_storage_access: ok');
