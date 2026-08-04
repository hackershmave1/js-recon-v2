// S1 — stable, persisted session id across service-worker respawns.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const modPath = path.resolve(__dirname, '../modules/session-store.js');
const source = fs.readFileSync(modPath, 'utf8').replace('export class SessionStore', 'class SessionStore');

// In-memory chrome.storage.local mock (async get/set, like the real API).
function makeStorage(initial = {}) {
  const data = { ...initial };
  return {
    _data: data,
    async get(key) {
      if (typeof key === 'string') return (key in data) ? { [key]: data[key] } : {};
      return { ...data };
    },
    async set(obj) { Object.assign(data, obj); }
  };
}

const sandbox = { console, crypto: globalThis.crypto, Date, Math };
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.SessionStore = SessionStore;`, sandbox, { filename: modPath });
const SessionStore = sandbox.SessionStore;

async function run() {
  // 1) First run: loadOrCreate mints + persists an id.
  {
    const storage = makeStorage();
    const store = new SessionStore(storage);
    const id1 = await store.loadOrCreate();
    assert.ok(id1 && typeof id1 === 'string', 'loadOrCreate returns an id');
    assert.equal(storage._data.reconSessionId, id1, 'id is persisted to storage');
  }

  // 2) Respawn: loadOrCreate returns the SAME persisted id (no rotation).
  {
    const storage = makeStorage({ reconSessionId: 'fixed-abc' });
    const store = new SessionStore(storage);
    assert.equal(await store.loadOrCreate(), 'fixed-abc', 'persisted id survives a respawn');
    assert.equal(await store.loadOrCreate(), 'fixed-abc', 'repeated loadOrCreate is stable');
  }

  // 3) Explicit new session: rotate mints a DIFFERENT id and persists it.
  {
    const storage = makeStorage({ reconSessionId: 'old-id' });
    const store = new SessionStore(storage);
    const rotated = await store.rotate();
    assert.notEqual(rotated, 'old-id', 'rotate produces a new id');
    assert.equal(storage._data.reconSessionId, rotated, 'rotated id is persisted');
    assert.equal(await store.load(), rotated, 'subsequent load returns the rotated id');
  }

  // 4) load() returns null when nothing is stored.
  {
    const store = new SessionStore(makeStorage());
    assert.equal(await store.load(), null, 'load() is null on empty storage');
  }

  console.log('test_s1_session_persistence: ok');
  process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
