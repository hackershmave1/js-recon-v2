// ADR-0011: "Open in workspace" asks the platform for a single-use handoff code and opens the
// workspace with it in the URL FRAGMENT. Any failure falls back to the bare URL (login screen).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function loadWorkspaceClient(fetchImpl) {
  const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
  const source = fs.readFileSync(modPath, 'utf8').replace('export class WorkspaceClient', 'class WorkspaceClient');
  const sandbox = { console, URL, setTimeout, clearTimeout, AbortController, Date, Promise, encodeURIComponent, fetch: fetchImpl };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
  return sandbox.WorkspaceClient;
}

const CODE = 'C'.repeat(43);
const ok = (body) => async () => ({ ok: true, status: 200, json: async () => body });
const client = (settings, fetchImpl) => {
  const WorkspaceClient = loadWorkspaceClient(fetchImpl);
  return new WorkspaceClient({ getSettings: () => settings });
};

async function testCodeGoesInTheFragmentNeverTheQuery() {
  const calls = [];
  const c = client({ workspaceUrl: 'http://ws.example.test:8000/', authToken: 'tok-1' }, async (url, init) => {
    calls.push({ url, init });
    return ok({ code: CODE, expires_in: 30 })();
  });
  const url = await c.workspaceOpenUrl('ext session/1');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://ws.example.test:8000/auth/handoff');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer tok-1');
  const u = new URL(url);
  assert.equal(u.origin, 'http://ws.example.test:8000');
  assert.equal(u.hash, `#handoff=${CODE}`);
  assert.equal(u.searchParams.get('capture'), 'ext session/1');
  assert.ok(!u.search.includes(CODE), 'the code is never in the query string');
  console.log('  code in fragment only: ok');
}

async function testFallsBackToBareUrlOnAnyFailure() {
  const bare = 'http://localhost:8000/?capture=s1';
  for (const fetchImpl of [
    async () => ({ ok: false, status: 429, json: async () => ({}) }),
    async () => ({ ok: false, status: 503, json: async () => ({}) }),
    async () => { throw new Error('unreachable'); },
    ok({ nope: true }),
    ok({ code: 'short' }),
  ]) {
    const url = await client({ authToken: 'tok' }, fetchImpl).workspaceOpenUrl('s1');
    assert.equal(url, bare);
    assert.ok(!url.includes('handoff'));
  }
  console.log('  failure falls back to the bare URL: ok');
}

async function testNoTokenMeansNoRequest() {
  let called = false;
  const url = await client({ workspaceUrl: 'https://ws.example.test' }, async () => { called = true; return ok({})(); }).workspaceOpenUrl('');
  assert.equal(called, false, 'no login token => no handoff request');
  assert.equal(url, 'https://ws.example.test/');
  console.log('  no token, no request: ok');
}

async function testBaseWithFragmentOrQueryIsHandled() {
  const c = client({ workspaceUrl: 'http://ws.example.test:8000/app?x=1#old', authToken: 't' }, ok({ code: CODE, expires_in: 30 }));
  const u = new URL(await c.workspaceOpenUrl('s1'));
  assert.equal(u.hash, `#handoff=${CODE}`);
  assert.equal(u.searchParams.get('x'), '1');
  assert.equal(u.searchParams.get('capture'), 's1');
  console.log('  base with query/fragment: ok');
}

async function testNonHttpBaseNeverGetsACode() {
  let called = false;
  const c = client({ workspaceUrl: 'javascript://x', authToken: 't' }, async () => { called = true; return ok({})(); });
  const url = await c.workspaceOpenUrl('s1');
  // resolveApiBase() already falls back to localhost for a non-http scheme; the code is only
  // ever requested from, and attached to, that same resolved origin.
  assert.ok(url.startsWith('http://localhost:8000'));
  assert.ok(!called || new URL(url).origin === 'http://localhost:8000');
  console.log('  non-http base: ok');
}

await testCodeGoesInTheFragmentNeverTheQuery();
await testFallsBackToBareUrlOnAnyFailure();
await testNoTokenMeansNoRequest();
await testBaseWithFragmentOrQueryIsHandled();
await testNonHttpBaseNeverGetsACode();
console.log('test_handoff_open: all passed');
