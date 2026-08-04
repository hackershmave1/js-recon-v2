// AuthContextTracker — record/consume/discard lifecycle of the request auth context
// (Authorization/Cookie/CSRF headers) that background.js delegates to this module.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const modPath = path.resolve(__dirname, '../modules/auth-context.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class AuthContextTracker', 'class AuthContextTracker');

// vm context needs the web APIs the module uses (URL); ECMAScript intrinsics
// (Map/Set/Date) are provided by the context itself.
const sandbox = { console, URL };
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.AuthContextTracker = AuthContextTracker;`, sandbox, { filename: modPath });
const AuthContextTracker = sandbox.AuthContextTracker;

function makeTracker(settings = { captureAuthContext: true }) {
  return new AuthContextTracker({
    getSettings: () => settings,
    isInScope: () => true,
    isExtensionRequest: () => false
  });
}

const makeDetails = (requestId) => ({
  requestId,
  url: 'https://app.example.com/main.js',
  requestHeaders: [
    { name: 'Authorization', value: 'Bearer abc123' },
    { name: 'Cookie', value: 'session=xyz; theme=dark' },
    { name: 'X-Ignored', value: 'nope' }
  ]
});

async function run() {
  // 1) record -> consume returns the captured context (single-use).
  {
    const tracker = makeTracker();
    tracker.record(makeDetails('req-1'));
    const ctx = tracker.consume('req-1', 'https://app.example.com/main.js');
    assert.ok(ctx, 'consume returns the recorded context');
    assert.equal(ctx.headers.authorization, 'Bearer abc123', 'allowlisted header captured');
    assert.equal(ctx.headers['x-ignored'], undefined, 'non-allowlisted header dropped');
    assert.equal(ctx.domain, 'app.example.com', 'domain derived from the request URL');
    // Compare via join() — the array is created in the vm realm, so deepStrictEqual would
    // fail on the cross-realm Array.prototype even when the contents match.
    assert.equal(ctx.cookie.names.join(','), 'session,theme', 'cookie names extracted');
    assert.equal(tracker.consume('req-1', 'https://app.example.com/main.js'), null, 'context is single-use');
  }

  // 2) consume after the TTL elapses returns null.
  {
    const tracker = makeTracker();
    tracker.record(makeDetails('req-2'));
    const entry = tracker.requestAuthContexts.get('req-2');
    entry.capturedAt = Date.now() - (10 * 60 * 1000); // older than the 5-min TTL
    assert.equal(tracker.consume('req-2', 'https://app.example.com/main.js'), null, 'expired context is not returned');
  }

  // 3) discard removes a stashed context before it can be consumed.
  {
    const tracker = makeTracker();
    tracker.record(makeDetails('req-3'));
    assert.equal(tracker.requestAuthContexts.size, 1, 'record stored one entry');
    tracker.discard('req-3');
    assert.equal(tracker.requestAuthContexts.size, 0, 'discard removed the entry');
    assert.equal(tracker.consume('req-3', 'https://app.example.com/main.js'), null, 'consume after discard is null');
  }

  console.log('test_auth_context: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
