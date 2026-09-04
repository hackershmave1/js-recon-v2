// D43a — a blackholed asset must not hang ContentFetcher.fetch forever. A per-attempt
// AbortController timeout aborts the fetch and returns {success:false} promptly, so the strictly
// serial capture queue (background.js processQueue) advances instead of stalling on one asset.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modPath = path.resolve(__dirname, '../modules/content-fetcher.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class ContentFetcher', 'class ContentFetcher');

// Static invariant: the timeout must actually be wired to the fetch signal (guards a silent
// regression that removes the AbortController but keeps the field).
assert.match(source, /signal:\s*controller\.signal/, 'fetch must pass the abort signal');
assert.match(source, /this\.fetchTimeoutMs/, 'a per-attempt fetch timeout must exist');

let fetchImpl = () => new Promise(() => {});
const sandbox = {
  console, setTimeout, clearTimeout, AbortController,
  fetch: (u, i) => fetchImpl(u, i)
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.ContentFetcher = ContentFetcher;`, sandbox, { filename: modPath });
const ContentFetcher = sandbox.ContentFetcher;

async function run() {
  // 1) A server that never responds but honors abort -> fetch fails promptly (not a hang).
  fetchImpl = (_url, init) => new Promise((_resolve, reject) => {
    const signal = init && init.signal;
    const onAbort = () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); };
    if (signal) { if (signal.aborted) onAbort(); else signal.addEventListener('abort', onAbort); }
  });
  const f = new ContentFetcher();
  f.maxRetries = 1;            // single attempt so the test stays fast (no backoff sleep)
  f.fetchTimeoutMs = 50;
  const start = Date.now();
  const res = await f.fetch('https://example.com/hang.js');
  assert.equal(res.success, false, 'a hung fetch resolves as failure, not a hang');
  assert.match(res.error, /timeout/i, 'the failure reason names the timeout');
  assert.ok(Date.now() - start < 2000, 'fetch aborts promptly instead of hanging');

  // 2) A successful fetch still returns content and caches it.
  fetchImpl = async () => ({ ok: true, headers: { get: () => 'identity' }, text: async () => 'console.log(1);' });
  const f2 = new ContentFetcher();
  f2.fetchTimeoutMs = 1000;
  const ok = await f2.fetch('https://example.com/app.js');
  assert.equal(ok.success, true, 'a healthy fetch succeeds');
  assert.equal(ok.content, 'console.log(1);', 'returns the body');
  const cached = await f2.fetch('https://example.com/app.js');
  assert.equal(cached.cached, true, 'second fetch is served from cache');

  console.log('test_content_fetch_timeout: ok');
  process.exit(0);
}
run().catch((e) => { console.error(e); process.exit(1); });
