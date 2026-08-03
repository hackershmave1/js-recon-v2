// session-store.js — durable session id for the capture pipeline.
//
// Why this exists: the MV3 service worker is torn down after ~30s idle and a fresh
// instance is constructed on the next event. The session id used to be minted in the
// JSExtractor constructor, so every respawn started a NEW backend session — one
// browse fragmented into many sessions, and (because the backend dedupes on
// (session_id, content_hash)) a durable upload queue could not safely resume.
// Persisting the id in chrome.storage.local keeps ONE session per engagement across
// respawns; it rotates only when the user explicitly starts a new session.
export class SessionStore {
  constructor(storage) {
    // Injectable for tests; defaults to chrome.storage.local.
    this.storage = storage || (typeof chrome !== 'undefined' ? chrome.storage.local : null);
    this.key = 'reconSessionId';
  }

  generate() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
  }

  async load() {
    const result = await this.storage.get(this.key);
    const id = result && result[this.key];
    return typeof id === 'string' && id ? id : null;
  }

  async save(id) {
    await this.storage.set({ [this.key]: id });
    return id;
  }

  // Restore the persisted id, or mint + persist a new one on first run.
  async loadOrCreate() {
    const existing = await this.load();
    if (existing) return existing;
    return this.save(this.generate());
  }

  // Explicit "new session" — a fresh id that replaces the persisted one.
  async rotate() {
    return this.save(this.generate());
  }
}
