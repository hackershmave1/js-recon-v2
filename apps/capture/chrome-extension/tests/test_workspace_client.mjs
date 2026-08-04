// WorkspaceClient.resolveApiBase — the single-source-of-truth derivation of the
// workspace API origin from settings.workspaceUrl that background.js delegates here.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const modPath = path.resolve(__dirname, '../modules/workspace-client.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class WorkspaceClient', 'class WorkspaceClient');

// resolveApiBase uses only string/regex ops; the fetch-based methods reference web APIs
// lazily (only when called), so a minimal context is enough to load the class.
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.WorkspaceClient = WorkspaceClient;`, sandbox, { filename: modPath });
const WorkspaceClient = sandbox.WorkspaceClient;

const withUrl = (workspaceUrl) => new WorkspaceClient({ getSettings: () => ({ workspaceUrl }) });

async function run() {
  // Prepends http:// to a scheme-less workspace URL.
  assert.equal(withUrl('localhost:3000').resolveApiBase(), 'http://localhost:3000', 'scheme-less URL gets http://');
  assert.equal(withUrl('recon.example.com').resolveApiBase(), 'http://recon.example.com', 'scheme-less host gets http://');

  // Keeps an explicit scheme and strips a trailing slash.
  assert.equal(withUrl('https://recon.example.com/').resolveApiBase(), 'https://recon.example.com', 'trailing slash stripped');
  assert.equal(withUrl('https://recon.example.com///').resolveApiBase(), 'https://recon.example.com', 'multiple trailing slashes stripped');

  // Scheme-less + trailing slash combined.
  assert.equal(withUrl('recon.example.com/').resolveApiBase(), 'http://recon.example.com', 'scheme-less + trailing slash');

  // Defaults to localhost when no workspace URL is set.
  assert.equal(withUrl('').resolveApiBase(), 'http://localhost:3000', 'empty workspace URL defaults to localhost');
  assert.equal(new WorkspaceClient({ getSettings: () => ({}) }).resolveApiBase(), 'http://localhost:3000', 'missing workspace URL defaults to localhost');

  console.log('test_workspace_client: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
