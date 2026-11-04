// C7 "the worker owns deadlines": every WorkspaceClient call's timer must cover reading the
// BODY, not just the headers. A server that sends headers and then stalls used to leave
// `await resp.json()` with no deadline, so the popup waited on a reply that never came.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class WorkspaceClient', 'class WorkspaceClient');

// Headers arrive at once; the body stalls until the request's signal aborts (as fetch does).
const stallingFetch = async (_url, init = {}) => ({
  ok: true,
  status: 200,
  json: () => new Promise((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => {
      reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    });
  }),
});
// Deadlines fire after 5 ms instead of seconds, so a regression fails fast instead of hanging.
const fastSetTimeout = (fn) => setTimeout(fn, 5);

const sandbox = { console, URL, AbortController, setTimeout: fastSetTimeout, clearTimeout, fetch: stallingFetch };
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
const client = new sandbox.WorkspaceClient({
  getSettings: () => ({ workspaceUrl: 'http://localhost:8000', authToken: 't' }),
  getSessionId: () => 's1',
  getObservations: () => [],
});

const guard = (promise, name) => Promise.race([
  promise,
  new Promise((_r, reject) => setTimeout(() => reject(new Error(`${name} hung past its deadline`)), 1000)),
]);

const calls = {
  login: () => client.login('u', 'p'),
  analyzeSession: () => client.analyzeSession(),
  getAnalysisProgress: () => client.getAnalysisProgress(),
  listProjects: () => client.listProjects(),
  getSessionFindingsSummary: () => client.getSessionFindingsSummary('s1'),
  createProject: () => client.createProject({ name: 'p' }),
  // (saveLlmConfig reads no body on success, so it cannot stall there.)
  testLlmConfig: () => client.testLlmConfig(),
};
for (const [name, call] of Object.entries(calls)) {
  const result = await guard(call(), name);
  assert.equal(result.error, 'timeout', `${name}: a stalled body read ends as a timeout, got ${JSON.stringify(result)}`);
  console.log(`✓ ${name}: a stalled body read times out instead of hanging`);
}
