export class BatchUploader {
  constructor() {
    this.pendingQueue = [];
    this.batchSize = 5;
    this.batchInterval = 5000;
    // Per-batch upload timeout. Chrome imposes no default fetch timeout, so without this
    // a blackholed workspace hangs upload() forever — which in turn stalls flushAll() and
    // the analyze trigger that awaits it. Overridable (tests set it small).
    this.uploadTimeoutMs = 30000;
    this.uploadTimer = null;
    this.apiEndpoint = this.normalizeEndpoint('http://localhost:3000/api/save-files');
    this.performAnalysisOnUpload = false;
    // Session scope (root domains + include-subdomains) chosen when a new session is
    // started; carried into save-files metadata so the backend seeds the session's scope.
    this.scope = null;
    // Resolved project binding + non-scope config snapshot chosen when a new session starts;
    // stamped onto save-files metadata so the backend binds it on session create (mirrors
    // this.scope / scopeMetadata). null keeps today's payload (no project keys).
    this.config = null;
    this.isUploading = false;
    this.isFlushing = false;
    // Durable outbox (IndexedDB) so queued uploads survive a service-worker respawn.
    // null keeps the old in-memory-only behaviour (used by unit tests without IDB).
    this.store = null;
    // Fired when the queue drains to empty so the owner can clear its flush alarm.
    this.onDrained = null;
    this.stats = {
      uploadedFiles: 0,
      failedBatches: 0,
      droppedFiles: 0,
      lastError: null,
      lastUploadAt: null
    };
  }

  // Outbox key includes the session id so the SAME content captured under two
  // different sessions occupies distinct entries — matching the backend dedupe
  // identity (session_id, content_hash). Keying by hash alone let a later session
  // overwrite an earlier session's still-unsent file.
  outboxKey(fileObject) {
    return `${fileObject.sessionId || ''}:${fileObject.contentHash}`;
  }

  async enqueue(fileObject) {
    this.pendingQueue.push(fileObject);
    // Persist BEFORE the network attempt so a worker teardown mid-flight cannot lose
    // the file.
    if (this.store) {
      try { await this.store.put(this.outboxKey(fileObject), fileObject); }
      catch (e) { console.warn('Outbox persist failed:', e); }
    }

    if (!this.uploadTimer) {
      this.uploadTimer = setTimeout(() => {
        this.processBatch();
      }, this.batchInterval);
    }

    if (this.pendingQueue.length >= this.batchSize) {
      this.processBatch();
    }
  }

  // Drop files from the durable outbox once they no longer need re-sending
  // (server accepted them, or they were permanently rejected).
  async forget(batch) {
    if (!this.store) return;
    for (const f of batch) {
      try { await this.store.delete(this.outboxKey(f)); }
      catch (e) { /* best effort — a stale entry is harmless (server dedupes) */ }
    }
  }

  setStore(store) { this.store = store || null; }
  setOnDrained(fn) { this.onDrained = typeof fn === 'function' ? fn : null; }

  // Reload any persisted-but-unsent files after a service-worker respawn and resume
  // draining. Returns the resulting pending count so the owner can arm its alarm.
  async rehydrate() {
    if (!this.store) return this.pendingQueue.length;
    let items = [];
    try { items = (await this.store.getAll()) || []; }
    catch (e) {
      // Fail SAFE: a read error is NOT "empty". Return a sentinel so the caller keeps
      // the flush alarm armed and retries, instead of orphaning the outbox.
      console.warn('Outbox rehydrate failed:', e);
      return -1;
    }

    const known = new Set(this.pendingQueue.map((f) => this.outboxKey(f)));
    for (const it of items) {
      const key = (it && it.contentHash) ? this.outboxKey(it) : null;
      if (key && !known.has(key)) {
        this.pendingQueue.push(it);
        known.add(key);
      }
    }
    if (this.pendingQueue.length > 0 && !this.uploadTimer && !this.isUploading) {
      this.uploadTimer = setTimeout(() => this.processBatch(), this.batchInterval);
    }
    return this.pendingQueue.length;
  }

  async processBatch() {
    if (this.isUploading || this.pendingQueue.length === 0) {
      return;
    }

    this.isUploading = true;
    clearTimeout(this.uploadTimer);
    this.uploadTimer = null;

    const batch = this.pendingQueue.splice(0, this.batchSize);

    try {
      await this.upload(batch);
      console.log(`Successfully uploaded batch of ${batch.length} files`);
      this.stats.uploadedFiles += batch.length;
      this.stats.lastError = null;
      this.stats.lastUploadAt = new Date().toISOString();
      await this.forget(batch);
    } catch (error) {
      this.stats.failedBatches += 1;
      this.stats.lastError = error.message;

      if (error.retriable === false) {
        // Permanent failure (4xx other than 429): the same bytes will never be
        // accepted, so re-queueing loops forever (the original bug). Drop the
        // batch, record it, and let later batches keep flowing.
        // NOTE: a whole batch is dropped even if only one file is invalid; since
        // the client now pre-caps size (<=10 MB), 4xx here is rare. Isolating the
        // single offender via per-file re-upload is a possible future refinement.
        console.error('Batch upload rejected (dropped, non-retriable):', error);
        this.stats.droppedFiles += batch.length;
        await this.forget(batch);
        chrome.notifications.create({
          type: 'basic',
          iconUrl: 'icons/icon48.png',
          title: 'Upload Rejected',
          message: `${batch.length} file(s) rejected by the server (${error.status || 'error'}) and skipped.`
        });
      } else {
        // Transient failure (network / 5xx / 429): put the batch back and retry.
        console.error('Batch upload failed (will retry):', error);
        this.pendingQueue.unshift(...batch);
        chrome.notifications.create({
          type: 'basic',
          iconUrl: 'icons/icon48.png',
          title: 'Upload Failed',
          message: `Failed to upload ${batch.length} files. Will retry.`
        });
      }
    } finally {
      this.isUploading = false;

      if (this.pendingQueue.length > 0 && !this.isFlushing) {
        this.uploadTimer = setTimeout(() => {
          this.processBatch();
        }, this.batchInterval);
      } else if (this.pendingQueue.length === 0 && this.onDrained) {
        // Outbox empty — let the owner clear the durable-flush alarm.
        this.onDrained();
      }
    }
  }

  async flushAll() {
    this.isFlushing = true;
    // Clear any sticky error from an earlier batch so a stale display value can't
    // linger while we drain.
    this.stats.lastError = null;
    clearTimeout(this.uploadTimer);
    this.uploadTimer = null;

    while (this.isUploading) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }

    while (this.pendingQueue.length > 0) {
      const before = this.pendingQueue.length;
      await this.processBatch();
      while (this.isUploading) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      // If the queue didn't shrink, the batch was re-queued (a transient failure) —
      // stop draining and let the timer retry later so we don't spin. An uploaded
      // OR dropped (non-retriable) batch shrinks the queue, so draining continues.
      if (this.pendingQueue.length >= before) {
        break;
      }
    }

    this.isFlushing = false;

    if (this.pendingQueue.length > 0) {
      this.uploadTimer = setTimeout(() => {
        this.processBatch();
      }, this.batchInterval);
    }
  }

  async upload(files) {
    if (!this.apiEndpoint) {
      throw new Error('API endpoint is not configured');
    }

    const payload = {
      metadata: {
        uploadDate: new Date().toISOString(),
        batchSize: files.length,
        version: '3.0.0',
        performAnalysis: this.performAnalysisOnUpload,
        // Decouple: when analyze-on-upload is OFF, explicitly disable ALL server-side
        // analysis (including the backend's smart-triggers, which `performAnalysis:false`
        // alone does NOT suppress) so bulk capture stays a fast store on the single-worker
        // backend. Analysis is then run on demand (POST /api/sessions/{id}/analyze/start).
        disableAnalysis: !this.performAnalysisOnUpload,
        // Explicit session scope (only honoured by save_files on session create).
        ...this.scopeMetadata(),
        // Project binding + resolved non-scope config snapshot (also create-only on the backend).
        ...this.configMetadata()
      },
      files: files
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.uploadTimeoutMs);
    let response;
    try {
      response = await fetch(this.apiEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
    } catch (e) {
      // Network error or timeout (abort). Both are transient → retriable so the batch is
      // re-queued rather than dropped.
      const error = new Error(e && e.name === 'AbortError'
        ? `Upload timed out after ${this.uploadTimeoutMs}ms`
        : (e && e.message ? e.message : 'Network error'));
      error.retriable = true;
      throw error;
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      const errorText = await response.text();
      const error = new Error(`HTTP ${response.status}: ${errorText}`);
      error.status = response.status;
      // 4xx (except 429 Too Many Requests) are permanent — the payload is invalid
      // and will never be accepted, so it must not be re-queued. Everything else
      // (network failure, 5xx, 429) is transient and safe to retry.
      error.retriable = !(response.status >= 400 && response.status < 500 && response.status !== 429);
      throw error;
    }

    return await response.json();
  }

  setEndpoint(url) {
    this.apiEndpoint = this.normalizeEndpoint(url);
  }

  setPerformAnalysisOnUpload(enabled) {
    this.performAnalysisOnUpload = enabled === true;
  }

  setScope(scope) {
    // scope: { rootDomains: string[], includeSubdomains: boolean } | null
    this.scope = (scope && typeof scope === 'object') ? scope : null;
  }

  scopeMetadata() {
    const out = {};
    if (this.scope && Array.isArray(this.scope.rootDomains) && this.scope.rootDomains.length) {
      out.rootDomains = this.scope.rootDomains;
    }
    if (this.scope && typeof this.scope.includeSubdomains === 'boolean') {
      out.includeSubdomains = this.scope.includeSubdomains;
    }
    return out;
  }

  setConfig(config) {
    // config: { projectId, captureConfig, overrideKeys } | null
    this.config = (config && typeof config === 'object') ? config : null;
  }

  configMetadata() {
    const out = {};
    if (!this.config) return out;
    if (this.config.projectId) out.projectId = this.config.projectId;   // null/'' omitted -> standalone
    if (this.config.captureConfig && typeof this.config.captureConfig === 'object') {
      out.captureConfig = this.config.captureConfig;
    }
    if (Array.isArray(this.config.overrideKeys)) out.overrideKeys = this.config.overrideKeys;
    return out;
  }

  normalizeEndpoint(url) {
    if (!url || typeof url !== 'string') {
      return 'http://localhost:3000/api/save-files';
    }

    try {
      const parsed = new URL(url.trim());
      parsed.pathname = '/api/save-files';
      parsed.search = '';
      parsed.hash = '';
      return parsed.toString();
    } catch (error) {
      return 'http://localhost:3000/api/save-files';
    }
  }

  getStats() {
    return {
      ...this.stats,
      pendingQueueLength: this.pendingQueue.length,
      endpoint: this.apiEndpoint,
      isUploading: this.isUploading
    };
  }
}
