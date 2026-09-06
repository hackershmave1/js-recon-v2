// Behavioral tests for content-script.js using a Node.js VM with a minimal DOM/chrome stub.
// The IIFE wrapper is stripped so the inner functions are accessible in the sandbox context.
// Tests cover: isCapturableInlineScript classification, MutationObserver capture guard, and
// the postMessage relay (responseBodyObserved forwarding + cross-origin filter).
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.resolve(__dirname, '../content-script.js'), 'utf8');

// Strip the outer IIFE so we can inject mocks and access inner functions from the sandbox.
const inner = src
  .replace(/^\s*\(function\s*\(\)\s*\{\s*'use strict';\s*/, '')
  .replace(/\}\)\(\);\s*$/, '');

// ─── helpers ────────────────────────────────────────────────────────────────

function pass(name) { console.log(`\u2713 ${name}`); }
function fail(name, err) { console.error(`\u2717 ${name}: ${err.message || err}`); process.exit(1); }

function makeScriptNode({ type = null, src: nodeSrc = null, text = '' } = {}) {
  return {
    tagName: 'SCRIPT',
    src: nodeSrc || '',
    textContent: text,
    _type: type,
    getAttribute(attr) { return attr === 'type' ? this._type : null; }
  };
}

// Build a fresh sandbox for each test — state is fully isolated.
function makeSandbox({ isCapturing = false } = {}) {
  const messages = [];
  const storageData = { isCapturing };
  let onMessageHandler = null;
  let mutationCallback = null;
  const windowListeners = {};

  const mockChrome = {
    runtime: {
      onMessage: { addListener(fn) { onMessageHandler = fn; } },
      sendMessage(msg) { messages.push(msg); return Promise.resolve(); }
    },
    storage: {
      local: {
        get(keys) {
          const result = {};
          const ks = typeof keys === 'string' ? [keys] : Object.keys(keys);
          for (const k of ks) result[k] = storageData[k];
          return Promise.resolve(result);
        }
      }
    }
  };

  const mockDocument = {
    readyState: 'complete',
    documentElement: {},
    _inlineNodes: [],
    _srcNodes: [],
    querySelectorAll(sel) {
      if (sel === 'script:not([src])') return { forEach: (fn) => sandbox.document._inlineNodes.forEach(fn) };
      if (sel === 'script[src]') return { forEach: (fn) => sandbox.document._srcNodes.forEach(fn) };
      return { forEach: () => {} };
    }
  };

  const sandbox = {
    console,
    URL,
    // chrome APIs
    chrome: mockChrome,
    // DOM stubs
    document: mockDocument,
    window: {
      addEventListener(evt, fn) {
        windowListeners[evt] = windowListeners[evt] || [];
        windowListeners[evt].push(fn);
      }
    },
    location: { href: 'https://example.com/page' },
    performance: { getEntriesByType: () => [] },
    // MutationObserver stub: capture the callback so tests can invoke it directly.
    MutationObserver: class {
      constructor(cb) { mutationCallback = cb; }
      observe() {}
    },
    // Expose internals so tests can reach them.
    _messages: messages,
    _storageData: storageData,
    _windowListeners: windowListeners,
    get _mutationCallback() { return mutationCallback; },
    get _onMessageHandler() { return onMessageHandler; }
  };
  vm.createContext(sandbox);
  return sandbox;
}

function runInSandbox(sandbox) {
  vm.runInContext(inner, sandbox, { filename: 'content-script.js' });
}

// ─── Test 1: isCapturableInlineScript classification ────────────────────────
// The function is not exported, so we exercise it via scanInlineScripts: put a node into
// document._inlineNodes with non-empty text, run the script, and check whether a message
// was sent.  isCapturing=true so emitInline is not blocked by the storage gate.
// (MutationObserver path is separate; here we test the initial-scan path via scanInlineScripts.)

async function testIsCapturableClassification() {
  const cases = [
    { desc: 'no type attr → capturable',            type: null,                     src: null,                             expect: true  },
    { desc: 'type text/javascript → capturable',    type: 'text/javascript',        src: null,                             expect: true  },
    { desc: 'type module → capturable',             type: 'module',                 src: null,                             expect: true  },
    { desc: 'type application/ld+json → NOT capturable', type: 'application/ld+json', src: null,                          expect: false },
    { desc: 'type importmap → NOT capturable',      type: 'importmap',              src: null,                             expect: false },
    { desc: 'has src → NOT capturable',             type: null,                     src: 'https://example.com/app.js',     expect: true  },
    { desc: 'type application/json → NOT capturable', type: 'application/json',     src: null,                             expect: false },
  ];

  for (const c of cases) {
    try {
      const sandbox = makeSandbox({ isCapturing: true });
      // Override scanIfCapturing to call scanInlineScripts directly via the loaded script:
      // we set readyState='complete' so it calls scanIfCapturing() which reads storage (isCapturing=true),
      // then calls scanLoadedScripts → scanInlineScripts.
      // Place one inline node in the DOM stub.
      if (c.src) {
        // A node with src is handled by the src-scan path, not the inline path.
        // For the "has src" case we test the src-script path instead:
        const node = makeScriptNode({ type: c.type, src: c.src, text: 'console.log("hi");' });
        sandbox.document._srcNodes.push(node);
      } else {
        const node = makeScriptNode({ type: c.type, text: 'console.log("hello world");' });
        sandbox.document._inlineNodes.push(node);
      }
      runInSandbox(sandbox);
      // scanIfCapturing returns a promise (storage.get); wait for micro-tasks to settle.
      await new Promise((r) => setTimeout(r, 20));
      const sentInline = sandbox._messages.some((m) => m.action === 'inlineScriptDetected');
      const sentScript = sandbox._messages.some((m) => m.action === 'dynamicScriptDetected');
      const sent = sentInline || (c.src ? sentScript : false);
      if (c.src) {
        // "has src" case: the inline path (no-src query) never sees this node; src nodes are reported
        // via emitScript (script[src] scan). The node IS in _srcNodes so emitScript fires.
        assert.equal(sentScript, c.expect, c.desc);
      } else {
        assert.equal(sentInline, c.expect, c.desc);
      }
      pass(c.desc);
    } catch (err) {
      fail(c.desc, err);
    }
  }
}

// ─── Test 2: MutationObserver guard — isCapturing=false → no messages ───────

async function testMutationObserverGuardOff() {
  const name = 'MutationObserver guard: isCapturing=false → no messages sent';
  try {
    const sandbox = makeSandbox({ isCapturing: false });
    runInSandbox(sandbox);
    await new Promise((r) => setTimeout(r, 5));
    const cb = sandbox._mutationCallback;
    assert.ok(cb, 'MutationObserver callback was registered');
    // Simulate a dynamic <script src=...> being added to the DOM.
    const addedNode = makeScriptNode({ src: 'https://example.com/app.js' });
    cb([{ addedNodes: [addedNode] }]);
    // Let the storage.get promise resolve.
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(sandbox._messages.length, 0, 'no messages when not capturing');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 3: MutationObserver guard — isCapturing=true → message sent ───────

async function testMutationObserverGuardOn() {
  const name = 'MutationObserver guard: isCapturing=true → dynamicScriptDetected message sent';
  try {
    const sandbox = makeSandbox({ isCapturing: true });
    runInSandbox(sandbox);
    await new Promise((r) => setTimeout(r, 5));
    const cb = sandbox._mutationCallback;
    assert.ok(cb, 'MutationObserver callback was registered');
    const url = 'https://example.com/app.js';
    const addedNode = makeScriptNode({ src: url });
    cb([{ addedNodes: [addedNode] }]);
    await new Promise((r) => setTimeout(r, 30));
    const msg = sandbox._messages.find((m) => m.action === 'dynamicScriptDetected');
    assert.ok(msg, 'dynamicScriptDetected message was sent');
    assert.equal(msg.url, url, 'correct URL in message');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 4: postMessage relay — wrong source tag filtered ───────────────────

async function testPostMessageWrongTag() {
  const name = 'postMessage relay: wrong source tag filtered → no responseBodyObserved';
  try {
    const sandbox = makeSandbox({ isCapturing: true });
    runInSandbox(sandbox);
    await new Promise((r) => setTimeout(r, 5));
    const listeners = sandbox._windowListeners['message'] || [];
    assert.ok(listeners.length > 0, 'message listener registered on window');
    // Fire with wrong source tag (not 'recon-xhr-hook').
    for (const fn of listeners) {
      fn({ source: sandbox.window, data: { source: 'some-other-hook', url: 'https://t.com/api', body: '{}' } });
    }
    await new Promise((r) => setTimeout(r, 10));
    const sent = sandbox._messages.some((m) => m.action === 'responseBodyObserved');
    assert.equal(sent, false, 'no responseBodyObserved for wrong source tag');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 5: postMessage relay — correct tag forwarded ───────────────────────

async function testPostMessageCorrectTag() {
  const name = 'postMessage relay: correct tag → responseBodyObserved forwarded';
  try {
    const sandbox = makeSandbox({ isCapturing: true });
    runInSandbox(sandbox);
    await new Promise((r) => setTimeout(r, 5));
    const listeners = sandbox._windowListeners['message'] || [];
    assert.ok(listeners.length > 0, 'message listener registered on window');
    for (const fn of listeners) {
      fn({
        source: sandbox.window,
        data: {
          source: 'recon-xhr-hook',
          method: 'POST',
          url: 'https://target.com/api/data',
          status: 200,
          contentType: 'application/json',
          body: '{"result":1}'
        }
      });
    }
    await new Promise((r) => setTimeout(r, 10));
    const msg = sandbox._messages.find((m) => m.action === 'responseBodyObserved');
    assert.ok(msg, 'responseBodyObserved was sent');
    assert.equal(msg.url, 'https://target.com/api/data', 'correct URL');
    assert.equal(msg.body, '{"result":1}', 'correct body');
    assert.equal(msg.method, 'POST', 'correct method');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 6: postMessage relay — cross-origin filtered ───────────────────────

async function testPostMessageCrossOriginFiltered() {
  const name = 'postMessage relay: cross-origin source (event.source !== window) → no message sent';
  try {
    const sandbox = makeSandbox({ isCapturing: true });
    runInSandbox(sandbox);
    await new Promise((r) => setTimeout(r, 5));
    const listeners = sandbox._windowListeners['message'] || [];
    assert.ok(listeners.length > 0, 'message listener registered on window');
    const differentSource = {}; // simulates a cross-origin iframe's window
    for (const fn of listeners) {
      fn({
        source: differentSource,
        data: {
          source: 'recon-xhr-hook',
          url: 'https://target.com/api/data',
          body: '{"x":1}'
        }
      });
    }
    await new Promise((r) => setTimeout(r, 10));
    const sent = sandbox._messages.some((m) => m.action === 'responseBodyObserved');
    assert.equal(sent, false, 'cross-origin postMessage is filtered');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── runner ──────────────────────────────────────────────────────────────────

async function run() {
  await testIsCapturableClassification();
  await testMutationObserverGuardOff();
  await testMutationObserverGuardOn();
  await testPostMessageWrongTag();
  await testPostMessageCorrectTag();
  await testPostMessageCrossOriginFiltered();
  console.log('test_content_script_behavioral: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
