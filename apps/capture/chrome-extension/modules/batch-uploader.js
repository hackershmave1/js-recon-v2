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
    this.apiEndpoint = this.normalizeEndpoint('http://localhost:8000/api/save-files');
    this.performAnalysisOnUpload = false;
    // Session scope (root domains + include-subdomains) chosen when a new session is
    // started; carried into save-files metadata so the backend seeds the session's scope.
    this.scope = null;
    // Resolved project binding + non-scope config snapshot chosen when a new session starts;
    // stamped onto save-files metadata so the backend binds it on session create (mirrors
    // this.scope / scopeMetadata). null keeps today's payload (no project keys).
    this.config = null;
    // Monotonic epoch, bumped by clearOutbox() (a tenant switch). A batch spliced for upload under
    // one epoch must NOT be re-queued after a clear bumped it — otherwise a previous tenant's
    // in-flight files would resurface and flush under the new tenant's token.
    this.epoch = 0;
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
    // The login-session Bearer token (from settings, pushed by background via setAuthToken).
    // null => unauthenticated ingest: the backend routes to the shared capture tenant.
    // A valid token routes save-files into the operator's own tenant.
    this.authToken = null;
    // Whether the LAST save-files ack reported the ingest as PAIRED (operator tenant).
    // null until the first upload under the current token; surfaced via getStats so the
    // popup can show a paired ✓/✗ instead of failing silently on a bad/expired token.
    this.lastPaired = null;
    // Auth-expiry pause (DEBT D41). A 401/403 from save-files means the login token expired or was
    // rejected mid-capture. Those batches must NOT be dropped (the original bug) — they are valid
    // once re-authenticated — so processBatch re-queues them and sets this flag to PAUSE the drain
    // (otherwise it would loop 401s). onAuthFailure lets the owner surface a "session expired —
    // sign in again" state + notification. Cleared by resumeUploads() (on re-login) and by
    // clearOutbox() (a tenant switch empties the queue anyway).
    this.authPaused = false;
    this.onAuthFailure = null;
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

  // Drop ALL pending work — the in-memory queue AND the durable store — so one tenant's unsent
  // captures can never flush under another tenant's token after a tenant switch. The outbox drains
  // under whatever token is current, so this is the tenant-isolation guard for a tenant change.
  async clearOutbox() {
    // Bump the epoch FIRST so any batch already spliced for upload (in flight) is treated as stale
    // and dropped instead of re-queued when it fails — closing the in-flight-retry leak path.
    this.epoch += 1;
    this.pendingQueue = [];
    // A full drop (tenant switch / new session) must not leave an auth-pause latched: there is
    // nothing left to re-send, and the next tenant's token drains fresh work (DEBT D41 / review R1).
    this.authPaused = false;
    if (this.store && typeof this.store.clear === 'function') {
      try { await this.store.clear(); } catch (e) { /* best-effort; the in-memory queue is already dropped */ }
    }
  }
  setOnDrained(fn) { this.onDrained = typeof fn === 'function' ? fn : null; }
  setOnAuthFailure(fn) { this.onAuthFailure = typeof fn === 'function' ? fn : null; }

  // Resume a drain paused by a 401/403 (DEBT D41). Called on a successful (re-)login. Clears the
  // pause UNCONDITIONALLY — deliberately NOT gated on a token change: auth.token.mint() stamps exp
  // at 1-second resolution over a fixed payload, so two logins within the same wall-clock second
  // produce a byte-identical token, and a transient 403 can pause with a still-valid token — either
  // would otherwise latch the pause forever (a silent upload outage). The owner is the sole caller
  // and only calls this after installing a fresh token, so an unconditional clear is safe.
  resumeUploads() {
    this.authPaused = false;
    if (this.pendingQueue.length > 0 && !this.isUploading) {
      // Clear any stale (paused, no-op) timer and drain promptly rather than waiting out a
      // leftover batchInterval timer that flushAll may have armed while paused.
      clearTimeout(this.uploadTimer);
      this.uploadTimer = setTimeout(() => this.processBatch(), 0);
    }
  }

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
    // Skip while an auth-pause is in effect (DEBT D41): the token is expired/rejected, so uploading
    // would just 401 again and re-pause. resumeUploads() (on re-login) lifts this.
    if (this.isUploading || this.pendingQueue.length === 0 || this.authPaused) {
      return;
    }

    this.isUploading = true;
    clearTimeout(this.uploadTimer);
    this.uploadTimer = null;

    const batch = this.pendingQueue.splice(0, this.batchSize);
    // Snapshot the epoch this batch belongs to; if clearOutbox() bumps it while we're in flight
    // (a tenant switch), a transient failure below must DROP the batch, not re-queue it.
    const batchEpoch = this.epoch;

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
      } else if (batchEpoch !== this.epoch) {
        // The outbox was cleared while this batch was in flight (a tenant switch bumped the
        // epoch). Re-queueing would resurface a previous tenant's files and flush them under the
        // new tenant's token — the exact cross-tenant leak we guard against. Drop, don't retry.
        console.warn('Dropping stale-epoch batch after outbox clear (tenant switch)');
        this.stats.droppedFiles += batch.length;
        await this.forget(batch);
      } else if (error.authExpired) {
        // Auth expired/rejected mid-flight (DEBT D41): DON'T drop — these bytes upload fine once
        // re-authenticated. Re-queue and PAUSE the drain (processBatch's guard skips while paused)
        // so we don't loop 401s; fire onAuthFailure so the owner can show a "session expired" state.
        // resumeUploads() (on re-login) lifts the pause and drains the backlog.
        console.warn('Upload rejected (auth expired) — pausing uploads, batch kept:', error.status);
        this.pendingQueue.unshift(...batch);
        this.authPaused = true;
        if (this.onAuthFailure) {
          try { this.onAuthFailure(error.status); } catch (e) { /* owner hook is best-effort */ }
        }
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

      if (this.pendingQueue.length > 0 && !this.isFlushing && !this.authPaused) {
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
    // A valid login token routes this batch into the operator's tenant; without one the
    // backend falls back to the shared capture tenant (no header => today's behavior).
    // Snapshot the token AT SEND: if it changes mid-flight (setAuthToken resets lastPaired),
    // this ack's `paired` must not be recorded against the new token (see below).
    const tokenAtSend = this.authToken;
    const headers = { 'Content-Type': 'application/json' };
    if (tokenAtSend) headers.Authorization = `Bearer ${tokenAtSend}`;
    let response;
    try {
      response = await fetch(this.apiEndpoint, {
        method: 'POST',
        headers,
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
      // 401/403 mean the login token expired or was rejected mid-capture (DEBT D41): those bytes are
      // valid once re-authenticated, so they are RETRIABLE (re-queued, never dropped) and flagged
      // authExpired so processBatch PAUSES the drain instead of looping. Other 4xx (not 429/401/403)
      // stay permanent — an invalid payload will never be accepted, so re-queueing loops forever.
      // Everything else (network failure, 5xx, 429) is transient and safe to retry.
      error.authExpired = response.status === 401 || response.status === 403;
      error.retriable = !(response.status >= 400 && response.status < 500 && ![429, 401, 403].includes(response.status));
      throw error;
    }

    const body = await response.json();
    // Record whether this ack routed to the operator tenant (save-files returns `paired`)
    // so the popup can reflect auth state instead of failing silently on a bad token —
    // but ONLY if the token is unchanged since send, so a late ack from a prior token can't
    // stamp a stale ✓/✗ onto a newer one (setAuthToken already reset it to null).
    if (this.authToken === tokenAtSend && body && typeof body.paired === 'boolean') {
      this.lastPaired = body.paired;
    }
    return body;
  }

  setEndpoint(url) {
    this.apiEndpoint = this.normalizeEndpoint(url);
  }

  // The login-session Bearer token. Normalize by keeping ONLY printable ASCII
  // (0x21–0x7e): the token is unpadded base64url ("<payload>.<sig>"), all printable
  // ASCII, whereas whitespace or a control char (NUL/LF/CR) in a header value makes fetch()
  // throw on an invalid header — which upload() would treat as a transient network error
  // and retry forever (a silent upload outage). Stripping non-printable-ASCII removes
  // exactly the dangerous bytes and can never corrupt a real token. Keep this method
  // IDENTICAL to workspace-client.authHeaders (no drift). Empty => null (no header => the
  // backend's shared-tenant fallback, i.e. today's behavior).
  setAuthToken(token) {
    const cleaned = String(token || '').replace(/[^!-~]+/g, '');
    const next = cleaned || null;
    // A token change re-arms the paired check so getStats can't report a stale
    // paired verdict from the previous token.
    if (next !== this.authToken) this.lastPaired = null;
    this.authToken = next;
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
      return 'http://localhost:8000/api/save-files';
    }

    try {
      const parsed = new URL(url.trim());
      parsed.pathname = '/api/save-files';
      parsed.search = '';
      parsed.hash = '';
      return parsed.toString();
    } catch (error) {
      return 'http://localhost:8000/api/save-files';
    }
  }

  getStats() {
    return {
      ...this.stats,
      pendingQueueLength: this.pendingQueue.length,
      endpoint: this.apiEndpoint,
      isUploading: this.isUploading,
      // Auth-expiry pause (DEBT D41): the single source of truth for the popup's "session expired"
      // banner. True => a 401/403 paused the drain; re-login (resumeUploads) clears it.
      authPaused: this.authPaused,
      paired: this.lastPaired,
      // The engagement the current session is bound to (null => Standalone). Surfaced so the
      // popup's Active-engagement display reflects the REAL binding the uploader stamps, not a
      // separate copy that could drift. Mirrors configMetadata()'s projectId source.
      projectId: (this.config && this.config.projectId) || null
    };
  }
}
