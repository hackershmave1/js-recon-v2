// Unit tests for the injectable projects cache (modules/projects-cache.js). Pure module;
// storage + fetcher are injected, so it imports directly with an in-memory storage fake.
import assert from 'node:assert/strict';
import { readCache, writeCache, listProjectsWithCache } from '../modules/projects-cache.js';

// In-memory chrome.storage.local mock: get accepts a string or string[] and returns only the
// requested keys (matching the real API); set merges.
function makeStorage(initial = {}) {
  const data = { ...initial };
  return {
    _data: data,
    async get(keys) {
      if (keys == null) return { ...data };
      const list = Array.isArray(keys) ? keys : [keys];
      const out = {};
      for (const k of list) if (k in data) out[k] = data[k];
      return out;
    },
    async set(obj) { Object.assign(data, obj); },
  };
}

async function run() {
  // 1) Live success refreshes the cache and reports source 'live'.
  {
    const storage = makeStorage();
    const projects = [{ id: 'p1', name: 'acme' }];
    const res = await listProjectsWithCache(async () => ({ success: true, projects }), storage, 1000);
    assert.deepEqual(res, { projects, source: 'live' });
    assert.deepEqual(storage._data.projectsCache, projects, 'cache written on live success');
    assert.equal(storage._data.projectsCacheAt, 1000, 'timestamp written');
  }

  // 2) Fetch throws -> fall back to the cached list, source 'cache'.
  {
    const cached = [{ id: 'p9', name: 'old' }];
    const storage = makeStorage({ projectsCache: cached, projectsCacheAt: 5 });
    const res = await listProjectsWithCache(async () => { throw new Error('unreachable'); }, storage);
    assert.deepEqual(res.projects, cached);
    assert.equal(res.source, 'cache');
  }

  // 3) A reachable-but-unsuccessful response also falls back to cache.
  {
    const cached = [{ id: 'p2', name: 'c' }];
    const storage = makeStorage({ projectsCache: cached, projectsCacheAt: 5 });
    const res = await listProjectsWithCache(async () => ({ success: false, error: 'HTTP 500' }), storage);
    assert.deepEqual(res.projects, cached);
    assert.equal(res.source, 'cache');
  }

  // 4) Failure with no cache -> empty list, source 'empty'.
  {
    const storage = makeStorage();
    const res = await listProjectsWithCache(async () => { throw new Error('x'); }, storage);
    assert.deepEqual(res.projects, []);
    assert.equal(res.source, 'empty');
  }

  // 5) read/write round-trip.
  {
    const storage = makeStorage();
    await writeCache(storage, [{ id: 'z' }], 42);
    assert.deepEqual(await readCache(storage), { projects: [{ id: 'z' }], cachedAt: 42 });
  }

  console.log('test_projects_cache: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
