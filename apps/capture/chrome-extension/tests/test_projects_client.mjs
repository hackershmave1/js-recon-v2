// WorkspaceClient.listProjects / createProject — GET/POST against /api/projects. Loaded via
// the repo's vm pattern (strip the single export) with a fetch stub, like test_t007.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class WorkspaceClient', 'class WorkspaceClient');

const calls = [];
function makeClient(fetchImpl) {
  const sandbox = { console, URL, setTimeout, clearTimeout, AbortController, fetch: fetchImpl };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
  return new sandbox.WorkspaceClient({ getSettings: () => ({ workspaceUrl: 'http://ws.test' }) });
}

async function run() {
  // listProjects maps a 200 + JSON array to { success:true, projects:[...] }.
  {
    const client = makeClient(async (url, init) => {
      calls.push({ url, method: (init && init.method) || 'GET' });
      return { ok: true, status: 200, json: async () => ([{ id: 'p1', name: 'acme' }]) };
    });
    const res = await client.listProjects();
    assert.equal(res.success, true);
    assert.deepEqual(res.projects, [{ id: 'p1', name: 'acme' }]);
    assert.equal(calls[0].url, 'http://ws.test/api/projects');
    assert.equal(calls[0].method, 'GET');
  }

  // Non-2xx -> { success:false }.
  {
    const client = makeClient(async () => ({ ok: false, status: 503, json: async () => ({}) }));
    const res = await client.listProjects();
    assert.equal(res.success, false);
  }

  // createProject POSTs the JSON body and maps 200 to { success:true, project }.
  {
    let captured;
    const client = makeClient(async (url, init) => {
      captured = { url, method: init.method, body: JSON.parse(init.body) };
      return { ok: true, status: 200, json: async () => ({ id: 'p2', name: captured.body.name }) };
    });
    const res = await client.createProject({ name: 'bounty', defaults: { scope: { rootDomains: ['a.com'] } } });
    assert.equal(res.success, true);
    assert.equal(res.project.id, 'p2');
    assert.equal(captured.method, 'POST');
    assert.equal(captured.url, 'http://ws.test/api/projects');
    assert.equal(captured.body.name, 'bounty');
  }

  console.log('test_projects_client: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
