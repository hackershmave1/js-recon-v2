// C7 popup<->worker contract: ONE reply shape ({ ok, error? }), one popup safety net, and every
// popup action answered by a worker handler. api.js runs in a vm sandbox (as test_popup_api.mjs);
// the worker side is pinned structurally (background.js is not importable in Node).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
const apiSource = fs.readFileSync(path.join(root, 'src/popup/api.js'), 'utf8')
  .replace(/^export (function|const|async function|let)/gm, '$1');
const bg = fs.readFileSync(path.join(root, 'background.js'), 'utf8');

function pass(name) { console.log(`✓ ${name}`); }

function loadApi(sendMessage, { lastError = null } = {}) {
  const runtime = { lastError, sendMessage, getManifest: () => ({ version: '1' }) };
  // Deadlines fire after 5 ms so the safety-net path is exercised without real waits.
  const sandbox = { chrome: { runtime, tabs: {} }, setTimeout: (fn) => setTimeout(fn, 5), clearTimeout, console };
  vm.createContext(sandbox);
  vm.runInContext(`${apiSource}\nthis.api = { send, setNoReplyHandler, replyError };`, sandbox);
  return sandbox.api;
}

// 1. A worker reply passes through unchanged.
{
  const api = loadApi((msg, cb) => cb({ ok: true, isCapturing: false }));
  const res = await api.send('getStatus');
  assert.equal(res.ok, true);
  assert.equal(res.isCapturing, false);
  pass('a worker reply passes through');
}

// 2. No receiver (worker spinning up / extension reloaded) is a failure, not an empty success.
{
  const runtime = { lastError: null, getManifest: () => ({ version: '1' }) };
  runtime.sendMessage = (msg, cb) => { runtime.lastError = { message: 'Receiving end does not exist.' }; cb(undefined); };
  const sandbox = { chrome: { runtime, tabs: {} }, setTimeout: (fn) => setTimeout(fn, 5), clearTimeout, console };
  vm.createContext(sandbox);
  vm.runInContext(`${apiSource}
this.api = { send };`, sandbox);
  const res = await sandbox.api.send('getStatus');
  assert.equal(res.ok, false);
  assert.equal(res.error, 'Receiving end does not exist.');
  pass('no receiver -> { ok: false, error }');
}

// 3. No reply at all: ONE safety net resolves "unknown outcome" and asks the app to re-sync.
{
  const api = loadApi(() => { /* the worker never answers */ });
  const resynced = [];
  api.setNoReplyHandler((action) => resynced.push(action));
  const res = await api.send('analyzeSession');
  assert.equal(res.ok, false);
  assert.equal(res.error, 'no_reply');
  assert.equal(res.unknownOutcome, true);
  assert.deepEqual([...resynced], ['analyzeSession']);
  assert.notEqual(api.replyError(res, 'Analyze failed'), 'Analyze failed', 'unknown outcome reads differently from a failure');
  pass('no reply -> { ok:false, error:"no_reply", unknownOutcome:true } + re-sync');
}

// 4. A reply that lands after the safety net fired is ignored (the popup already re-synced).
{
  let late;
  const api = loadApi((msg, cb) => { late = cb; });
  const res = await api.send('getFiles');
  late({ ok: true, files: [] });
  assert.equal(res.error, 'no_reply');
  pass('a late reply after the deadline is ignored');
}

// 5. Every action the popup sends has a worker handler.
{
  const actions = [...apiSource.matchAll(/send\('([A-Za-z]+)'/g)].map((m) => m[1]);
  assert.ok(actions.length >= 15, `found ${actions.length} popup actions`);
  const start = bg.indexOf('const handlers = {');
  const handlerBlock = bg.slice(start, bg.indexOf('};', start));
  for (const action of actions) {
    assert.match(handlerBlock, new RegExp(`\\b${action}:`), `worker has a handler for popup action ${action}`);
  }
  pass(`all ${actions.length} popup actions have a worker handler`);
}

// 6. The popup reads ONE verdict field: `ok` (no `.success` left in popup code).
{
  const popupFiles = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(js|jsx)$/.test(entry.name)) popupFiles.push(full);
    }
  };
  walk(path.join(root, 'src/popup'));
  const offenders = popupFiles.filter((f) => /\.success\b|success:\s*(true|false)/.test(fs.readFileSync(f, 'utf8')));
  assert.deepEqual(offenders.map((f) => path.relative(root, f)), []);
  pass('popup code reads only `ok`');
}

// 7. Worker wiring: every responding message goes through the one envelope, a throwing handler
// still answers, and stopCapture answers BEFORE the flush (the flush can take minutes).
{
  assert.match(bg, /import \{[^}]*replyOnce[^}]*\} from '\.\/modules\/message-reply\.js'/, 'background imports the envelope');
  assert.match(bg, /const reply = replyOnce\(sendResponse\);/, 'one reply per message');
  assert.match(bg, /dispatchReply\(\(\) => this\.handleMessage\(request, sender, reply\), reply\)/, 'handlers run under dispatchReply');
  const stop = bg.slice(bg.indexOf('async stopCapture('), bg.indexOf('async stopCapture(') + 900);
  assert.ok(stop.indexOf('sendResponse(') >= 0 && stop.indexOf('sendResponse(') < stop.indexOf('flushAll()'),
    'stopCapture replies before awaiting flushAll');
  assert.doesNotMatch(bg, /notifyUI/, 'the dead notifyUI broadcast (no listener) is gone');
  pass('worker wiring: envelope, dispatch, stopCapture order, no notifyUI');
}
