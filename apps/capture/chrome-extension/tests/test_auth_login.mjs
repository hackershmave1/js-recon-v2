// Central login (recon.auth) on the extension: WorkspaceClient.login() posts credentials to
// /auth/login and returns the session token + identity; authHeaders() rides the login
// session token as Bearer (the backend verifies it).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function loadWorkspaceClient(fetchImpl) {
  const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
  const source = fs
    .readFileSync(modPath, 'utf8')
    .replace('export class WorkspaceClient', 'class WorkspaceClient');
  const sandbox = {
    console, URL, setTimeout, clearTimeout, AbortController, Date, Promise, encodeURIComponent,
    fetch: fetchImpl
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
  return sandbox.WorkspaceClient;
}

async function testAuthHeaderRidesAuthToken() {
  const WorkspaceClient = loadWorkspaceClient(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  // Compare the Authorization string, not the whole object: authHeaders() builds its object
  // inside the vm sandbox, so a cross-realm deepEqual would fail on the differing prototype.
  const auth = (settings) => new WorkspaceClient({ getSettings: () => settings }).authHeaders().Authorization;

  assert.equal(auth({ authToken: 'auth-1' }), 'Bearer auth-1', 'login token rides as Bearer');
  assert.equal(auth({}), undefined, 'no token => no header (shared-tenant fallback)');
  assert.equal(auth({ authToken: '  a\nb ' }), 'Bearer ab', 'auth token whitespace/control stripped');

  console.log('  authHeaders rides authToken: ok');
}

async function testLogin() {
  const calls = [];
  let resp = {
    ok: true, status: 200,
    json: async () => ({ token: 'tok.sig', user: 'admin', role: 'admin', tenant: { id: 't1', name: 'QA' } })
  };
  const WorkspaceClient = loadWorkspaceClient(async (url, init) => { calls.push({ url, init }); return resp; });
  const wc = new WorkspaceClient({ getSettings: () => ({ workspaceUrl: 'http://localhost:8000' }) });

  const ok = await wc.login('admin', 'admin');
  assert.equal(ok.success, true, 'login success');
  assert.equal(ok.token, 'tok.sig', 'returns the session token');
  assert.equal(ok.tenant.name, 'QA', 'returns the tenant identity');
  const call = calls.at(-1);
  assert.ok(call.url.endsWith('/auth/login'), 'posts to /auth/login');
  assert.equal(call.init.method, 'POST', 'POST');
  assert.deepEqual(JSON.parse(call.init.body), { username: 'admin', password: 'admin' }, 'sends credentials in the body');
  assert.equal(call.init.headers['Content-Type'], 'application/json', 'json content-type');
  assert.equal(call.init.headers.Authorization, undefined, 'login is pre-auth: no Bearer');

  // A 401 maps to a structured failure the popup can message.
  resp = { ok: false, status: 401, json: async () => ({ detail: 'invalid credentials' }) };
  const bad = await wc.login('admin', 'wrong');
  assert.equal(bad.success, false, '401 => failure');
  assert.equal(bad.status, 401, 'propagates the status');
  assert.equal(bad.error, 'invalid credentials', 'surfaces the server detail');

  console.log('  login(): ok');
}

async function run() {
  await testAuthHeaderRidesAuthToken();
  await testLogin();
  console.log('test_auth_login: ok');
  process.exit(0);
}
run().catch((e) => { console.error(e); process.exit(1); });
