// idb-store.js — tiny promise-wrapped IndexedDB key/value store (one DB, one store).
//
// Backs the durable upload outbox and the dedup set so capture survives MV3
// service-worker teardown: queued uploads and the "already captured" set live on
// disk, not just in the worker's memory. chrome.storage.local is fine for small
// scalars (the session id), but the outbox holds full file bodies (up to 10 MB
// each), which is IndexedDB's job. Tests inject an in-memory adapter of the same
// shape ({ put, delete, clear, getAll }) instead of a real IDB.
export class IdbStore {
  constructor(dbName, storeName = 'kv') {
    this.dbName = dbName;
    this.storeName = storeName;
    this._dbPromise = null;
  }

  _db() {
    if (!this._dbPromise) {
      this._dbPromise = new Promise((resolve, reject) => {
        const req = indexedDB.open(this.dbName, 1);
        req.onupgradeneeded = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains(this.storeName)) {
            db.createObjectStore(this.storeName);
          }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
    return this._dbPromise;
  }

  async _tx(mode, run) {
    const db = await this._db();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(this.storeName, mode);
      const store = tx.objectStore(this.storeName);
      const req = run(store);
      let result;
      if (req) req.onsuccess = () => { result = req.result; };
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  }

  put(key, value) { return this._tx('readwrite', (s) => s.put(value, key)); }
  delete(key) { return this._tx('readwrite', (s) => s.delete(key)); }
  clear() { return this._tx('readwrite', (s) => s.clear()); }
  getAll() { return this._tx('readonly', (s) => s.getAll()); }
}
