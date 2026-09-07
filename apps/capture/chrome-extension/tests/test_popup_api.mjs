// popup/api.js — unit tests for the thin chrome.* message relay.
// Uses Node's vm module to inject a minimal chrome mock, matching the pattern in
// test_workspace_client.mjs and test_auth_context_scope.mjs. No npm dependencies.
//
// NOTE: vm sandbox objects have a different Object.prototype than the host context.
// We use JSON.stringify equality for plain-object assertions (works across vm
// boundaries), and strict assert.equal for scalar values.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../src/popup/api.js');
// Strip ES module export keywords so functions land in the vm sandbox.
const source = fs.readFileSync(modPath, 'utf8')
  .replace(/^export (function|const|async function)/gm, '$1');

function pass(name) { console.log(`\u2713 ${name}`); }
function fail(name, err) { console.error(`\u2717 ${name}: ${err.message || err}`); process.exit(1); }

// JSON-stringify equality for cross-vm plain-object comparisons.
function assertJsonEqual(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new assert.AssertionError({ message: `${msg}: expected ${e}, got ${a}`, actual: a, expected: e, operator: 'jsonEqual' });
  }
}

// ─── helpers ────────────────────────────────────────────────────────────────

// Build a fresh sandbox with controllable chrome stubs.
// sendMessageImpl: (msg, callback) => void — lets each test drive the callback.
// tabsQueryImpl: () => Promise<tab[]>
function makeSandbox({ sendMessageImpl = null, tabsQueryImpl = null } = {}) {
  const mockChrome = {
    runtime: {
      lastError: null,
      sendMessage(msg, callback) {
        if (sendMessageImpl) {
          sendMessageImpl(msg, callback);
        } else {
          callback(undefined);
        }
      },
      getManifest() { return { version: '1.0.0' }; }
    },
    tabs: {
      query(opts) {
        if (tabsQueryImpl) return tabsQueryImpl(opts);
        return Promise.resolve([]);
      },
      create() {}
    }
  };

  // Minimal document stub for downloadJson (anchor-click path).
  const appendedChildren = [];
  const removedChildren = [];
  const mockDocument = {
    createElement(tag) {
      return { tagName: tag, href: '', download: '', rel: '', _clicked: false,
        click() { this._clicked = true; },
        remove() { removedChildren.push(this); }
      };
    },
    body: {
      appendChild(el) { appendedChildren.push(el); }
    }
  };

  const createdObjectURLs = [];

  // A URL constructor that behaves like the real one but tracks createObjectURL calls.
  // We need to expose the constructor as both a regular function AND the static methods.
  function MockURL(href) { return new globalThis.URL(href); }
  MockURL.createObjectURL = function(blob) {
    const u = 'blob:test-' + createdObjectURLs.length;
    createdObjectURLs.push(u);
    return u;
  };
  MockURL.revokeObjectURL = function(u) { /* suppress */ };

  const sandbox = {
    console,
    Blob,
    URL: MockURL,
    chrome: mockChrome,
    document: mockDocument,
    setTimeout(fn, ms) { /* suppress revoke timer in tests */ },
    _appendedChildren: appendedChildren,
    _removedChildren: removedChildren,
    _createdObjectURLs: createdObjectURLs,
  };
  vm.createContext(sandbox);
  vm.runInContext(
    `${source}\nthis._send = send;\nthis._getActiveTabHost = getActiveTabHost;\nthis._downloadJson = downloadJson;`,
    sandbox,
    { filename: modPath }
  );
  return sandbox;
}

// ─── Test 1: send() — callback receives undefined → resolves to {} ──────────

async function testSendUndefinedCallbackResolvesToEmpty() {
  const name = 'send(): callback receives undefined → resolves to {}';
  try {
    const sb = makeSandbox({
      sendMessageImpl: (msg, cb) => { cb(undefined); }
    });
    const result = await sb._send('getStatus');
    assertJsonEqual(result, {}, 'undefined callback arg must resolve to {}');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 2: send() — callback receives null → resolves to {} ───────────────

async function testSendNullCallbackResolvesToEmpty() {
  const name = 'send(): callback receives null → resolves to {}';
  try {
    const sb = makeSandbox({
      sendMessageImpl: (msg, cb) => { cb(null); }
    });
    const result = await sb._send('getStatus');
    assertJsonEqual(result, {}, 'null callback arg must resolve to {}');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 3: send() — chrome.runtime.sendMessage throws (context invalidated) ─

async function testSendThrowsResolvesToEmpty() {
  const name = 'send(): chrome.runtime.sendMessage throws (context torn down) → resolves to {}';
  try {
    const sb = makeSandbox({
      sendMessageImpl: () => { throw new Error('Extension context invalidated.'); }
    });
    const result = await sb._send('getStatus');
    assertJsonEqual(result, {}, 'thrown sendMessage must resolve to {}');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 4: send() — callback receives a real response object ───────────────

async function testSendReturnsResponseObject() {
  const name = 'send(): callback receives response object → resolves with it';
  try {
    const responseFixture = { isCapturing: true, uploadedFiles: 3 };
    const sb = makeSandbox({
      sendMessageImpl: (msg, cb) => { cb(responseFixture); }
    });
    const result = await sb._send('getStatus');
    assertJsonEqual(result, responseFixture, 'response object must pass through');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 5: send() — action is forwarded in the message payload ─────────────

async function testSendForwardsAction() {
  const name = 'send(): action is forwarded as message.action';
  try {
    let captured = null;
    const sb = makeSandbox({
      sendMessageImpl: (msg, cb) => { captured = msg; cb({}); }
    });
    await sb._send('startCapture');
    assert.equal(captured?.action, 'startCapture', 'message.action must match the sent action');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 6: send() — extra payload fields are merged into the message ────────

async function testSendMergesExtraFields() {
  const name = 'send(): extra payload fields are merged into the message';
  try {
    let captured = null;
    const sb = makeSandbox({
      sendMessageImpl: (msg, cb) => { captured = msg; cb({}); }
    });
    await sb._send('login', { username: 'alice', password: 's3cr3t' });
    assert.equal(captured?.action, 'login', 'action preserved');
    assert.equal(captured?.username, 'alice', 'username merged');
    assert.equal(captured?.password, 's3cr3t', 'password merged');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 7: getActiveTabHost() — returns hostname on success ─────────────────

async function testGetActiveTabHostSuccess() {
  const name = 'getActiveTabHost(): returns hostname when active tab has a URL';
  try {
    const sb = makeSandbox({
      tabsQueryImpl: () => Promise.resolve([{ url: 'https://target.example.com/path?q=1' }])
    });
    const host = await sb._getActiveTabHost();
    assert.equal(host, 'target.example.com', 'must extract hostname from tab URL');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 8: getActiveTabHost() — returns '' when tab has no URL ─────────────

async function testGetActiveTabHostNoUrl() {
  const name = "getActiveTabHost(): returns '' when active tab has no URL";
  try {
    const sb = makeSandbox({
      tabsQueryImpl: () => Promise.resolve([{ url: null }])
    });
    const host = await sb._getActiveTabHost();
    assert.equal(host, '', 'missing URL must return empty string');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 9: getActiveTabHost() — returns '' when tabs.query returns empty ───

async function testGetActiveTabHostNoTabs() {
  const name = "getActiveTabHost(): returns '' when no active tab";
  try {
    const sb = makeSandbox({
      tabsQueryImpl: () => Promise.resolve([])
    });
    const host = await sb._getActiveTabHost();
    assert.equal(host, '', 'empty tab list must return empty string');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 10: getActiveTabHost() — returns '' when tabs.query throws ─────────

async function testGetActiveTabHostThrows() {
  const name = "getActiveTabHost(): returns '' when chrome.tabs.query throws";
  try {
    const sb = makeSandbox({
      tabsQueryImpl: () => Promise.reject(new Error('No tab access'))
    });
    const host = await sb._getActiveTabHost();
    assert.equal(host, '', 'thrown tabs.query must return empty string');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── Test 11: downloadJson() — creates anchor, clicks it, and removes it ─────

async function testDownloadJsonCreatesAnchorAndClicks() {
  const name = 'downloadJson(): creates an anchor, sets href+download, clicks and removes it';
  try {
    const sb = makeSandbox();
    sb._downloadJson({ foo: 'bar' }, 'export.json');
    const anchors = sb._appendedChildren;
    assert.equal(anchors.length, 1, 'exactly one anchor appended to document.body');
    const anchor = anchors[0];
    assert.ok(anchor.href.startsWith('blob:'), 'anchor href must be a blob URL');
    assert.equal(anchor.download, 'export.json', 'anchor download attribute must be the filename');
    assert.equal(anchor.rel, 'noopener', 'anchor rel must be noopener');
    assert.equal(anchor._clicked, true, 'anchor.click() must have been called');
    assert.equal(sb._removedChildren.length, 1, 'anchor.remove() must have been called');
    assert.equal(sb._createdObjectURLs.length, 1, 'URL.createObjectURL must have been called once');
    pass(name);
  } catch (err) { fail(name, err); }
}

// ─── runner ────────────────────────────────────────────────────────────────

async function run() {
  await testSendUndefinedCallbackResolvesToEmpty();
  await testSendNullCallbackResolvesToEmpty();
  await testSendThrowsResolvesToEmpty();
  await testSendReturnsResponseObject();
  await testSendForwardsAction();
  await testSendMergesExtraFields();
  await testGetActiveTabHostSuccess();
  await testGetActiveTabHostNoUrl();
  await testGetActiveTabHostNoTabs();
  await testGetActiveTabHostThrows();
  await testDownloadJsonCreatesAnchorAndClicks();
  console.log('test_popup_api: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
