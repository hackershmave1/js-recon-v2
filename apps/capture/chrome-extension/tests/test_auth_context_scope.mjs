// AuthContextTracker — scope/settings enforcement and sanitization guards.
// Complements test_auth_context.mjs (lifecycle) with tests that focus on the three
// capture gates (isInScope, captureAuthContext, isExtensionRequest), the CRLF-injection
// sanitizer, the cookie-name cap, and the maxAuthContextEntries eviction.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../modules/auth-context.js');
// Use VM (same technique as test_auth_context.mjs) so the ES module export keyword
// doesn't require a dynamic import dance while keeping the file unchanged.
const source = fs.readFileSync(modPath, 'utf8').replace('export class AuthContextTracker', 'class AuthContextTracker');
const sandbox = { console, URL };
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.AuthContextTracker = AuthContextTracker;`, sandbox, { filename: modPath });
const AuthContextTracker = sandbox.AuthContextTracker;

function pass(name) { console.log(`\u2713 ${name}`); }
function fail(name, err) { console.error(`\u2717 ${name}: ${err.message || err}`); process.exit(1); }

const AUTH_HEADER = [{ name: 'Authorization', value: 'Bearer tok123' }];
const TARGET_URL = 'https://target.com/api.js';

// ─── Test 1: out-of-scope URL → nothing recorded ─────────────────────────────

function testOutOfScopeNotRecorded() {
  const name = 'out-of-scope URL → nothing recorded';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => false,
      isExtensionRequest: () => false,
      getSettings: () => ({ captureAuthContext: true })
    });
    tracker.record({ requestId: '1', url: 'https://out-of-scope.com/app.js', requestHeaders: AUTH_HEADER });
    assert.equal(tracker.requestAuthContexts.size, 0, 'out-of-scope URL must not be recorded');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 2: captureAuthContext: false → nothing recorded ────────────────────

function testSettingDisabledNotRecorded() {
  const name = 'captureAuthContext: false → nothing recorded';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => true,
      isExtensionRequest: () => false,
      getSettings: () => ({ captureAuthContext: false })
    });
    tracker.record({ requestId: '1', url: TARGET_URL, requestHeaders: AUTH_HEADER });
    assert.equal(tracker.requestAuthContexts.size, 0, 'captureAuthContext:false must suppress recording');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 3: isExtensionRequest → nothing recorded ───────────────────────────

function testExtensionRequestNotRecorded() {
  const name = 'isExtensionRequest → nothing recorded';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => true,
      isExtensionRequest: () => true,
      getSettings: () => ({ captureAuthContext: true })
    });
    tracker.record({ requestId: '1', url: TARGET_URL, requestHeaders: AUTH_HEADER });
    assert.equal(tracker.requestAuthContexts.size, 0, 'extension requests must not be recorded');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 4: sanitizeAuthHeaderValue — CRLF injection stripped ───────────────

function testSanitizeCrlfStripped() {
  const name = 'sanitizeAuthHeaderValue: CRLF injection stripped';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => true,
      isExtensionRequest: () => false,
      getSettings: () => ({})
    });
    const result = tracker.sanitizeAuthHeaderValue('Bearer tok\r\nX-Injected: evil');
    assert.ok(!result.includes('\r'), 'CR must be removed');
    assert.ok(!result.includes('\n'), 'LF must be removed');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 5: sanitizeAuthHeaderValue — truncates at 8192 chars ───────────────

function testSanitizeTruncation() {
  const name = 'sanitizeAuthHeaderValue: truncates at 8192 chars';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => true,
      isExtensionRequest: () => false,
      getSettings: () => ({})
    });
    const long = 'x'.repeat(9000);
    const result = tracker.sanitizeAuthHeaderValue(long);
    assert.equal(result.length, 8192, 'must truncate at 8192');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 6: extractCookieNames — caps at 64 ─────────────────────────────────

function testExtractCookieNamesCap() {
  const name = 'extractCookieNames: caps at 64';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => true,
      isExtensionRequest: () => false,
      getSettings: () => ({})
    });
    const cookies = Array.from({ length: 70 }, (_, i) => `cookie${i}=val`).join('; ');
    const names = tracker.extractCookieNames(cookies);
    assert.equal(names.length, 64, 'must cap cookie names at 64');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 7: maxAuthContextEntries eviction ───────────────────────────────────
// Fill the map beyond the cap, then call record() (which calls pruneRequestAuthContexts
// internally). The map must shrink back to at or below maxAuthContextEntries.

function testMaxEntriesEviction() {
  const name = 'maxAuthContextEntries eviction: excess entries pruned on record()';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => true,
      isExtensionRequest: () => false,
      getSettings: () => ({ captureAuthContext: true })
    });
    const cap = tracker.maxAuthContextEntries;
    // Directly inject cap+5 entries with fresh timestamps to avoid TTL expiry
    // (pruneRequestAuthContexts also evicts by TTL, so use recent capturedAt).
    for (let i = 0; i < cap + 5; i++) {
      tracker.requestAuthContexts.set(`req-${i}`, {
        capturedAt: Date.now(),
        context: { headers: {}, domain: 'example.com', cookie: { present: false, names: [], count: 0 } }
      });
    }
    assert.ok(tracker.requestAuthContexts.size > cap, 'setup: map exceeds cap');
    // record() triggers pruneRequestAuthContexts via the normal path.
    tracker.record({
      requestId: 'trigger',
      url: TARGET_URL,
      requestHeaders: AUTH_HEADER
    });
    assert.ok(
      tracker.requestAuthContexts.size <= cap,
      `map must not exceed maxAuthContextEntries (${cap}) after prune; got ${tracker.requestAuthContexts.size}`
    );
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── Test 8: happy path — in-scope URL with Authorization header recorded correctly ─────

function testHappyPathRecord() {
  const name = 'happy path: in-scope URL with Authorization header recorded correctly';
  try {
    const tracker = new AuthContextTracker({
      isInScope: () => true,
      isExtensionRequest: () => false,
      getSettings: () => ({ captureAuthContext: true })
    });
    tracker.record({ requestId: 'r1', url: TARGET_URL, requestHeaders: [{ name: 'Authorization', value: 'Bearer abc123' }] });
    assert.equal(tracker.requestAuthContexts.size, 1, 'one entry recorded');
    const entry = tracker.requestAuthContexts.get('r1');
    assert.ok(entry, 'entry exists for r1');
    assert.equal(entry.context.headers['authorization'], 'Bearer abc123', 'authorization header stored (lowercased key)');
    assert.equal(entry.context.domain, 'target.com', 'domain derived from URL');
    pass(name);
  } catch (err) {
    fail(name, err);
  }
}

// ─── runner ───────────────────────────────────────────────────────────────────

function run() {
  testOutOfScopeNotRecorded();
  testSettingDisabledNotRecorded();
  testExtensionRequestNotRecorded();
  testSanitizeCrlfStripped();
  testSanitizeTruncation();
  testExtractCookieNamesCap();
  testMaxEntriesEviction();
  testHappyPathRecord();
  console.log('test_auth_context_scope: ok');
  process.exit(0);
}

run();
