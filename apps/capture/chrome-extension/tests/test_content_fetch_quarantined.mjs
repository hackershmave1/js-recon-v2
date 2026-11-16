// A quarantined script is fetched without credentials and is size-capped (ADR-0010).
import assert from 'node:assert/strict';
import { ContentFetcher } from '../modules/content-fetcher.js';

const inits = [];
globalThis.fetch = async (url, init) => {
  inits.push({ url, init });
  const body = url.includes('big') ? 'x'.repeat(50) : 'console.log(1)';
  return {
    ok: !url.includes('404'),
    status: url.includes('404') ? 404 : 200,
    headers: { get: (h) => (h.toLowerCase() === 'content-length' && url.includes('declared') ? '999999999' : null) },
    text: async () => body,
  };
};

const f = new ContentFetcher();

const ok = await f.fetchQuarantined('https://cdn.example/a.js');
assert.deepEqual(ok, { success: true, content: 'console.log(1)' });
assert.equal(inits[0].init.credentials, 'omit', 'never the researcher\'s cookies or HTTP auth for an unknown host');
assert.equal(inits[0].init.method, 'GET');

// One attempt only: a miss is a miss (no retry loop on the serial queue).
inits.length = 0;
assert.equal((await f.fetchQuarantined('https://cdn.example/404.js')).success, false);
assert.equal(inits.length, 1);

// Size cap: a declared length, or an actual body, over the cap is refused.
assert.deepEqual(await f.fetchQuarantined('https://cdn.example/declared.js', { maxBytes: 100 }),
  { success: false, error: 'too_large' });
assert.deepEqual(await f.fetchQuarantined('https://cdn.example/big.js', { maxBytes: 10 }),
  { success: false, error: 'too_large' });

// Not cached: it must never shadow a real fetch after the host is added.
assert.equal(f.cache.size, 0);

// The existing in-scope fetch is unchanged: it still sends credentials.
inits.length = 0;
await f.fetch('https://acme.io/app.js');
assert.equal(inits[0].init.credentials, 'include');

console.log('ok - quarantined fetch: credentials omitted, one attempt, size-capped');
