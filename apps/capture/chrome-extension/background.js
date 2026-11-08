// background.js - Main service worker
import { ContentFetcher } from './modules/content-fetcher.js';
import { DependencyExtractor } from './modules/dependency-extractor.js';
import { SourceMapDetector } from './modules/sourcemap-detector.js';
import { Decompressor } from './modules/decompressor.js';
import { BatchUploader } from './modules/batch-uploader.js';
import { SessionStore } from './modules/session-store.js';
import { IdbStore } from './modules/idb-store.js';
import { AuthContextTracker } from './modules/auth-context.js';
import { WorkspaceClient } from './modules/workspace-client.js';
import { buildExportData } from './modules/export-builder.js';
import { classifyAsset, isThirdParty, matchesDenylist, countSecrets } from './modules/asset-classifier.js';
import { listProjectsWithCache } from './modules/projects-cache.js';
import { settingsFromConfig } from './modules/project-config.js';
import { normalizeRootDomains } from './modules/normalize-scope.js';
import { isRelevantInlineScript } from './modules/inline-relevance.js';
import { normalizeObservedUrl, isApiIshObservation, isTelemetryPath } from './modules/observation-filter.js';
import { prepareRequestBody, redactBody, capBody } from './modules/body-capture.js';
import { CaptureSession } from './modules/capture-session.js';
import { PAGE_ACTIONS, admitMessage } from './modules/message-sender.js';
import { restrictStorageAccess, publishCaptureState } from './modules/storage-access.js';
import { replyOnce, dispatchReply } from './modules/message-reply.js';
import { hookMatchPatterns, hostInScope } from './modules/hook-matches.js';
import { authMarker, tokenExpiry } from './modules/token-validity.js';
import { CaptureApi } from './modules/capture-api.js';
import { CaptureLifecycle, badgeText } from './modules/capture-lifecycle.js';

// Seed denylist shown in the redesigned popup Settings on first run.
const DEFAULT_DENY_RULES = [
  { tag: 'CMS', pattern: '/wp-content/plugins/*' },
  { tag: 'CMS', pattern: '/wp-includes/*' },
  { tag: 'TRACK', pattern: '*.google-analytics.com' },
  { tag: 'TRACK', pattern: '*.doubleclick.net' },
  { tag: 'LIB', pattern: '*/jquery*.min.js' }
];


// Messages the popup/content-script send without waiting for a response. The onMessage
// listener must NOT hold the response channel open for these (see setupListeners).
const FIRE_AND_FORGET_ACTIONS = PAGE_ACTIONS;

// Per-page cap on captured inline <script> bodies (DEBT D45a). A churny SPA can inject a fresh
// inline script per route; the content-hash dedup + relevance filter thin most, this bounds the
// tail so one busy origin can't flood the outbox.
const INLINE_PER_PAGE_CAP = 100;

// Cap on distinct runtime request observations kept per session (DEBT D45b1). Observations are
// tiny ({method, url}) and deduped; this bounds a chatty SPA's long-poll / notification churn.
const OBSERVATION_CAP = 1000;

// Body-capture caps (DEBT D45b2): per-body char caps + a per-session total so captured bodies
// can't bloat the analyze payload. Beyond the total, observations still carry method+url (endpoint
// confirmation keeps working), just no body.
const REQ_BODY_CAP = 64 * 1024;
const RESP_BODY_CAP = 128 * 1024;
const BODY_TOTAL_CAP = 2 * 1024 * 1024;

class JSExtractor {
  constructor() {
    this.processingTimer = null;
    this.isCapturing = false;
    // One-shot guard so a run of 401s during an auth-expiry episode fires a single "session
    // expired" notification, not one per failed batch (DEBT D41). Reset on a successful (re-)login
    // and on every session transition.
    this.authNotified = false;
    this.settings = null;
    this.sessionStore = new SessionStore();

    this.limits = {
      // NOTE: must not exceed the backend's per-file cap (SecurityValidator.
      // MAX_JS_CONTENT_SIZE = 10 MB in api/app/security_utils.py). A larger file
      // is 422-rejected by /api/save-files, and a rejected file used to poison the
      // upload batch into an infinite retry. Byte cap ≤ 10 MB guarantees the server
      // (which caps by char count) accepts it. Oversized assets are soft-skipped
      // per-file via the popup "Max asset size" slider (maxAssetMb), not here.
      maxFileBytes: 10 * 1024 * 1024,
      maxTotalBytes: 200 * 1024 * 1024,
      maxFiles: 2000
    };
    
    this.contentFetcher = new ContentFetcher();
    this.dependencyExtractor = new DependencyExtractor();
    this.sourceMapDetector = new SourceMapDetector();
    this.decompressor = new Decompressor();
    this.batchUploader = new BatchUploader();
    // Request auth-context capture (Authorization/Cookie/CSRF headers), extracted into
    // its own module; the tracker is fed the live settings + scope/extension predicates.
    this.authTracker = new AuthContextTracker({
      getSettings: () => this.settings,
      isInScope: (url) => this.isInScope(url),
      isExtensionRequest: (details) => this.isExtensionRequest(details)
    });
    // Workspace backend client (health / analyze / progress + API-base resolution).
    this.workspaceClient = new WorkspaceClient({
      getSettings: () => this.settings,
      getSessionId: () => this.session.sessionId,
      batchUploader: this.batchUploader,
      // Runtime request observations to ship with analyze/start (DEBT D45b1).
      getObservations: () => this.session.observations,
      // Capture lifecycle: Analyze and progress name the capture in progress (set after construction).
      getRunId: () => (this.capture && this.capture.state ? this.capture.state.runId : null),
    });
    // Durable stores (IndexedDB) that outlive the service worker: the upload outbox
    // (unsent files) and the dedup set (hash -> {url, capturedAt}). Separate DBs to
    // avoid multi-store upgrade coordination.
    this.outboxStore = new IdbStore('recon-outbox');
    this.dedupStore = new IdbStore('recon-dedup');
    // Tracks whether the durable-flush alarm is armed, so per-file reconcile calls
    // don't re-create it on every capture.
    this.flushAlarmArmed = false;
    // Per-session state, its persistence and the new-session / standalone / clear transitions.
    // Built after its dependencies and before listeners attach (they read it only behind `ready`).
    this.session = new CaptureSession({
      storage: chrome.storage.local,
      sessionStore: this.sessionStore,
      dedupStore: this.dedupStore,
      uploader: this.batchUploader,
      authTracker: this.authTracker,
      getSettings: () => this.settings,
      observationCap: OBSERVATION_CAP
    });
    // The capture lifecycle (Start → End = one platform run): worker-owned state, see the module.
    this.captureApi = new CaptureApi({
      base: () => this.workspaceClient.resolveApiBase(),
      headers: () => this.workspaceClient.authHeaders(),
    });
    this.capture = new CaptureLifecycle({
      local: chrome.storage.local,
      session: chrome.storage.session,
      api: this.captureApi,
      captureSession: this.session,
      uploader: this.batchUploader,
      applyGate: (gate) => this.applyCaptureGate(gate),
      getObservations: () => this.session.observations,
    });
    // Resolves when initialize() completes; the bootstrap replaces this with the real
    // init promise. Listener handlers await it so a cold-woken worker finishes loading
    // settings/session/stores before handling the event that woke it.
    this.ready = Promise.resolve();
  }

  // D46(c): return the persisted capture history list — only the signed-in tenant's rows.
  async getHistory(sendResponse) {
    try {
      sendResponse({ history: await this.session.history(this.settings?.authTenantId) });
    } catch (e) {
      sendResponse({ history: [] });
    }
  }

  buildProcessingError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  recordProcessingFailure(reason, url, message) {
    this.session.processingStats.failedFiles += 1;
    this.session.processingStats.lastFailureReason = reason;
    this.session.processingStats.lastFailureUrl = url || null;
    this.session.processingStats.lastFailureMessage = message || null;
    this.updateBadge();
  }

  async initialize() {
    // Every start: keep the token/settings store away from content scripts (modules/storage-access.js).
    await restrictStorageAccess(chrome.storage).catch((e) => console.warn('storage access level', e));
    this.settings = await this.loadSettings();
    // Register the opt-in main-world response-body hook if it's enabled (DEBT D45b2).
    // Awaited so the XHR hook is registered before any requests arrive after a respawn.
    await this.syncResponseBodyHook();
    this.batchUploader.setEndpoint(this.workspaceClient.resolveApiBase());
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    // Re-apply the persisted Bearer token so a service-worker respawn keeps routing the
    // operator's captures into their tenant (the uploader holds it in memory, not storage).
    this.batchUploader.setAuthToken(this.settings.authToken);
    // A token already known to be expired would only 401: hold the outbox until re-login instead
    // of sending a doomed batch (and a fresh "session expired" notification) on every respawn.
    if (authMarker(this.settings.authToken, { trustLocalExpiry: !this.settings.localExpiryUntrusted }).authExpired) {
      this.batchUploader.pauseForAuth();
      await this.notifyExpiredOncePerToken();
    }
    // Re-apply a persisted scope so uploads keep tagging the session even if the
    // service worker recycled after a new session was started.
    if (this.settings.useDomainScope && Array.isArray(this.settings.domainScopes) && this.settings.domainScopes.length) {
      this.batchUploader.setScope({
        rootDomains: normalizeRootDomains(this.settings.domainScopes),
        includeSubdomains: this.settings.includeSubdomains !== false
      });
    }
    // Resume the SAME backend session a previous worker instance was using (id, engagement
    // binding, dedup set, popup counters, observations) instead of fragmenting into a new one.
    await this.session.restore();
    this.isCapturing = this.settings.isCapturing || false;
    // storage.session is cleared on browser restart: re-publish the page-visible capture bit.
    await publishCaptureState(chrome.storage, this.isCapturing).catch(() => {});
    // A capture in progress owns the gate: restored (paused after a browser restart, not after a
    // 30 s worker respawn), and a pending End resumes (modules/capture-lifecycle.js).
    this.batchUploader.setOnCaptureEnded((runId) => this.capture.endedRemotely(runId));
    await this.capture.recover();

    // --- durability wiring (S2): back the uploader with the persistent outbox and
    // let it clear the flush alarm once fully drained.
    this.batchUploader.setStore(this.outboxStore);
    this.batchUploader.setOnDrained(() => this.reconcileFlushAlarm(false));
    // Auth-expiry (DEBT D41): when a 401/403 pauses the uploader, surface a "session expired"
    // notification + badge. The uploader keeps the batch (re-queued); re-login resumes the drain.
    this.batchUploader.setOnAuthFailure((status) => this.handleAuthExpired(status));
    // Persist delivery stats on every change so the health panel survives service-worker respawns
    // (Fix 9). Stored separately from session state so a session rotation doesn't reset lifetime counts.
    this.batchUploader.setOnStatsChange((stats) => {
      chrome.storage.local.set({ uploadStats: {
        uploadedFiles: stats.uploadedFiles,
        droppedFiles: stats.droppedFiles,
        failedBatches: stats.failedBatches,
        lastError: stats.lastError || null,
        lastUploadAt: stats.lastUploadAt || null
      }}).catch(() => {});
    });
    // Restore persisted delivery stats so the health panel doesn't reset on every respawn.
    const { uploadStats } = await chrome.storage.local.get('uploadStats');
    if (uploadStats) this.batchUploader.restoreStats(uploadStats);

    // NOTE: listeners are registered SYNCHRONOUSLY at module load (see bootstrap at the
    // bottom), not here — MV3 tears the worker down and routes the waking event only to
    // listeners present in the first turn. Their handlers gate on `this.ready`.

    // Resume any uploads a previous worker instance left unsent.
    // rehydrate() returns pending count, or -1 if the outbox READ failed. Treat both
    // "has files" and "unknown" as reasons to keep the flush alarm armed (fail safe).
    const pendingUploads = await this.batchUploader.rehydrate();
    this.reconcileFlushAlarm(pendingUploads !== 0);

    console.log('JSExtractor initialized', {
      sessionId: this.session.sessionId,
      pendingUploads
    });
  }

  // Registered synchronously at module load (bootstrap below). Because a cold-woken
  // worker may not have finished initialize() yet, each handler defers its state-using
  // work behind `this.ready` (the initialize() promise). The webRequest detail objects
  // are plain data, so they stay valid inside the deferred continuation.
  setupListeners() {
    chrome.webRequest.onBeforeSendHeaders.addListener(
      // Gate on capture being active (the record() method no longer owns that check).
      (details) => { this.ready.then(() => { if (this.isCapturing) this.authTracker.record(details); }); },
      {
        urls: ["<all_urls>"],
        types: ["script"]
      },
      ["requestHeaders", "extraHeaders"]
    );

    chrome.webRequest.onCompleted.addListener(
      (details) => { this.ready.then(() => this.handleRequest(details)); },
      {
        urls: ["<all_urls>"],
        types: ["script"]
      },
      ["responseHeaders"]
    );

    // Runtime API-call observations (DEBT D45b1): XHR/fetch completions. We record ONLY
    // { method, url } for CONFIRMING endpoints — never the response body (that is the opt-in
    // main-world path). Same client-side scope/denylist gate as script capture.
    chrome.webRequest.onCompleted.addListener(
      (details) => { this.ready.then(() => this.recordObservation(details)); },
      {
        urls: ["<all_urls>"],
        types: ["xmlhttprequest"]
      },
      ["responseHeaders"]
    );

    // Request BODIES (DEBT D45b2, on by default): onBeforeRequest is the only webRequest hook that
    // exposes the request payload (GraphQL query / JSON) — no main world needed. Captured keyed by
    // requestId and attached to the observation at onCompleted; redacted + capped in captureRequestBody.
    chrome.webRequest.onBeforeRequest.addListener(
      (details) => { this.ready.then(() => this.captureRequestBody(details)); },
      {
        urls: ["<all_urls>"],
        types: ["xmlhttprequest"]
      },
      ["requestBody"]
    );

    // Drop a captured request body if the request errors (never completes) so the map can't leak.
    chrome.webRequest.onErrorOccurred.addListener(
      (details) => { this.ready.then(() => this.session.pendingRequestBodies.delete(details.requestId)); },
      {
        urls: ["<all_urls>"],
        types: ["xmlhttprequest"]
      }
    );

    chrome.webRequest.onErrorOccurred.addListener(
      // Gate on ready so discard can't run before the (also-deferred) record for the same
      // requestId in the cold pre-init window, which would orphan an auth-context entry.
      (details) => { this.ready.then(() => this.authTracker.discard(details.requestId)); },
      {
        urls: ["<all_urls>"],
        types: ["script"]
      }
    );

    chrome.runtime.onMessage.addListener(
      (request, sender, sendResponse) => {
        // A web page can forge content-script messages: reject a command from the wrong sender
        // before any handler runs (modules/message-sender.js).
        if (!admitMessage(request, sender, chrome.runtime, sendResponse)) return false;
        // Defer handling until init has populated settings/session/stores. Hold the
        // sendResponse channel open ONLY for actions that actually respond — a fire-and-
        // forget message (e.g. dynamicScriptDetected) returning true would leak the port
        // until GC and log "message port closed before a response was received".
        if (FIRE_AND_FORGET_ACTIONS.has(request && request.action)) {
          this.ready.then(() => this.handleMessage(request, sender, () => {}));
          return false;
        }
        // C7: every popup reply goes through ONE envelope ({ ok, error?, ...detail }), exactly once,
        // and a throwing/rejecting handler still answers instead of leaving the popup waiting.
        const reply = replyOnce(sendResponse);
        this.ready.then(() => dispatchReply(() => this.handleMessage(request, sender, reply), reply));
        return true;
      }
    );

    // Wake on browser start / install so initialize() closes storage.local to content scripts
    // (modules/storage-access.js) before the first page loads, not on the first captured request.
    chrome.runtime.onStartup.addListener(() => { this.ready.then(() => {}); });
    chrome.runtime.onInstalled.addListener(() => { this.ready.then(() => {}); });

    chrome.alarms.onAlarm.addListener((alarm) => {
      // Cold-respawn safety net: if the worker was torn down with unsent uploads, this
      // wakes it (min period ~30s per chrome.alarms) to drain the outbox. During active
      // capture the stream of webRequest events + the 5s timer already handle draining.
      if (alarm && alarm.name === 'flushOutbox') {
        this.ready.then(() => this.batchUploader.processBatch());
      }
    });
  }

  // Keep the durable-flush alarm alive only while unsent uploads exist, so an idle
  // extension isn't waking the worker every 30-60s for nothing.
  reconcileFlushAlarm(hasPending) {
    try {
      if (hasPending) {
        if (this.flushAlarmArmed) return; // already armed — avoid per-file churn
        this.flushAlarmArmed = true;
        chrome.alarms.create('flushOutbox', { periodInMinutes: 1 });
      } else {
        // Always clear (idempotent) so a stale alarm from a prior worker can't linger.
        this.flushAlarmArmed = false;
        chrome.alarms.clear('flushOutbox');
      }
    } catch (e) {
      // alarms API unavailable (e.g. permission missing) — non-fatal.
    }
  }

  // Noise denylist + out-of-scope "exclude" mode → drop before capture.
  shouldSkipUrl(url, documentUrl) {
    // Never capture the recon workspace's own assets — the tool must not recon itself. Safety
    // net even when scope is wide-open (e.g. after "Open Workspace" loads the workspace at localhost:8000).
    if (this.isWorkspaceUrl(url)) {
      return true;
    }
    if (matchesDenylist(url, this.settings.denyRules || [], this.settings.denyDefaultProfile !== false)) {
      return true;
    }
    if (this.settings.outOfScopeMode === 'exclude' && documentUrl && isThirdParty(url, documentUrl)) {
      return true;
    }
    return false;
  }

  // True if the URL is served by the configured RECON Workspace origin (workspaceUrl / API base),
  // so the extension never captures its own workspace/API JS. Fails open (false) on a bad URL.
  isWorkspaceUrl(url) {
    try {
      return new URL(url).origin === new URL(this.workspaceClient.resolveApiBase()).origin;
    } catch (e) {
      return false;
    }
  }

  async handleRequest(details) {
    if (!this.isCapturing) return;
    if (this.isExtensionRequest(details)) return;
    if (!this.isInScope(details.url)) {
      // Out-of-scope SCRIPT (the webRequest listener filters types:["script"], so every drop here
      // is a script the page loaded). Record the host as a discovery hint (D44) before dropping, so
      // the popup can surface a CDN-apex/separate host that served app JS and offer a one-click add.
      this.noteOutOfScopeScript(details.url);
      return;
    }
    if (this.shouldSkipUrl(details.url, details.documentUrl)) return;

    const authContext = this.authTracker.consume(details.requestId, details.url);
    const fileMetadata = this.extractMetadata(details, authContext);
    
    this.session.processingQueue.push({
      metadata: fileMetadata,
      tabId: details.tabId,
      frameId: details.frameId
    });

    this.scheduleQueueProcessing();
  }

  async processQueue() {
    if (this.session.processingQueue.length === 0) return;

    const batch = this.session.processingQueue.splice(0, 10);
    
    for (const item of batch) {
      try {
        await this.processFile(item);
      } catch (error) {
        console.error('Failed to process file:', item.metadata.url, error);
        this.recordProcessingFailure(
          error?.code || 'processing_failed',
          item?.metadata?.url,
          error?.message || 'Unknown processing error'
        );
      }
    }

    if (this.session.processingQueue.length > 0) {
      setTimeout(() => this.processQueue(), 100);
    }
  }

  async processFile(item) {
    const { metadata, tabId, frameId } = item;
    const url = metadata.url;

    console.log('Processing:', url);

    let contentResult;
    if (typeof metadata.inlineContent === 'string') {
      // Inline <script> body already read from the DOM (DEBT D45a) — the synthetic URL isn't
      // fetchable, so use the content in hand and skip the network fetch entirely.
      contentResult = { success: true, content: metadata.inlineContent };
    } else {
      contentResult = await this.contentFetcher.fetch(url, {});

      if (!contentResult.success) {
        if (tabId >= 0) {
          const fallback = await this.fetchViaContentScript(tabId, frameId, url);
          if (fallback.success) {
            contentResult = fallback;
          } else {
            throw this.buildProcessingError(
              'fetch_failed',
              `Failed to fetch: ${contentResult.error}`
            );
          }
        } else {
          throw this.buildProcessingError(
            'fetch_failed',
            `Failed to fetch: ${contentResult.error}`
          );
        }
      }
    }

    let content = contentResult.content;
    let contentEncoding = contentResult.contentEncoding || metadata.contentEncoding || 'identity';

    if (this.needsDecompression(url, contentEncoding, contentResult.isBinary)) {
      const decompressed = await this.decompressor.decompress(
        content,
        contentEncoding
      );
      if (!decompressed.success) {
        console.error('Decompression failed:', url, decompressed.error);
        this.recordProcessingFailure('decompress_failed', url, decompressed.error);
        return;
      }
      content = decompressed.content;
      contentEncoding = 'identity';
    }

    let sourceMapData = null;
    let sourceMapUrl = null;
    let detectedSourceMapUrl = null;
    // How the map ref was found: 'comment' | 'header' | 'probe' (DEBT D45c). Null when none.
    let sourceMapDetection = null;
    let sourceMapFetchStatus = this.settings.captureSourceMaps ? 'not_detected' : 'disabled';
    let sourceMapFetchError = null;
    if (this.settings.captureSourceMaps && typeof metadata.inlineContent !== 'string') {
      // 1) inline `//# sourceMappingURL=` comment (authoritative, existing path). Skipped for
      // inline <script> bodies (DEBT D45a) — they carry no separate map ref, and probing the
      // page URL + '.map' would be a wasted request per inline block.
      detectedSourceMapUrl = this.sourceMapDetector.detect(content, url);
      if (detectedSourceMapUrl) {
        sourceMapDetection = 'comment';
      } else {
        // 2) the `SourceMap:`/`X-SourceMap` response header the browser already gave us but
        // detection never consulted (DEBT D45c). metadata.headers keys are lowercased.
        detectedSourceMapUrl = this.sourceMapDetector.detectFromHeaders(metadata.headers, url);
        if (detectedSourceMapUrl) sourceMapDetection = 'header';
      }

      if (!detectedSourceMapUrl) {
        sourceMapFetchStatus = 'not_detected';
      } else if (detectedSourceMapUrl.startsWith('data:')) {
        try {
          const decoded = this.decodeDataUrl(detectedSourceMapUrl);
          sourceMapData = JSON.parse(decoded);
          sourceMapUrl = detectedSourceMapUrl;
          sourceMapFetchStatus = 'fetched';
        } catch (e) {
          sourceMapFetchStatus = 'parse_failed';
          sourceMapFetchError = e.message;
          console.error('Failed to parse data URI source map:', e);
        }
      } else {
        let sourceMapResult = await this.contentFetcher.fetch(detectedSourceMapUrl, {});
        if (!sourceMapResult.success && tabId >= 0) {
          const fallback = await this.fetchViaContentScript(tabId, frameId, detectedSourceMapUrl);
          if (fallback.success) {
            sourceMapResult = fallback;
          }
        }

        if (sourceMapResult.success) {
          try {
            sourceMapData = JSON.parse(sourceMapResult.content);
            sourceMapUrl = detectedSourceMapUrl;
            sourceMapFetchStatus = 'fetched';
          } catch (e) {
            sourceMapFetchStatus = 'parse_failed';
            sourceMapFetchError = e.message;
            console.error('Failed to parse source map:', e);
          }
        } else {
          sourceMapFetchStatus = this.classifySourceMapError(sourceMapResult.error);
          sourceMapFetchError = sourceMapResult.error || 'Fetch failed';
        }
      }

      // 3) last resort — probe the conventional `<file>.js.map` sibling (DEBT D45c). Only
      // when neither a comment nor a header pointed at a map. ONE request, NO retry (review
      // Finding A) since a miss is the common case on the strictly-serial queue. Accept only
      // a body that parses as a real map so a SPA 200-HTML fallback can't masquerade as one.
      if (!sourceMapData && sourceMapFetchStatus === 'not_detected') {
        const probeUrl = this.sourceMapDetector.conventionalMapUrl(url);
        if (probeUrl) {
          const probed = await this.contentFetcher.fetchOnce(probeUrl);
          if (probed.success) {
            try {
              const parsed = JSON.parse(probed.content);
              if (parsed && (parsed.version || parsed.sources || parsed.mappings)) {
                sourceMapData = parsed;
                sourceMapUrl = probeUrl;
                sourceMapDetection = 'probe';
                sourceMapFetchStatus = 'fetched';
              }
            } catch (e) {
              // not JSON → no conventional map here; stay 'not_detected' (silent, expected).
            }
          }
        }
      }
    }

    const dependencies = this.settings.resolveDependencies
      ? this.dependencyExtractor.extract(content, url)
      : [];

    const contentByteLength = this.getContentByteLength(content);

    // Per-asset size cap (popup "Max asset size" slider). Skips the single file
    // without stopping capture — unlike the hard limits in enforceLimits().
    const maxAssetBytes = (this.settings.maxAssetMb || 10) * 1024 * 1024;
    if (contentByteLength > maxAssetBytes) {
      this.recordProcessingFailure(
        'asset_too_large',
        url,
        `Asset ${(contentByteLength / 1048576).toFixed(1)} MB exceeds ${this.settings.maxAssetMb} MB limit`
      );
      return;
    }

    if (!this.enforceLimits(contentByteLength)) {
      return;
    }

    // Cheap, count-only enrichment for the redesigned popup. Runs only AFTER the
    // size gates above; we persist counts only — never the matched secret values.
    const classification = classifyAsset(url);
    const thirdParty = isThirdParty(url, metadata.documentUrl || metadata.initiator);
    const secretCount = countSecrets(content);

    const contentHash = await this.calculateHash(content);

    // Version-aware deduplication - check if we already have this exact content
    if (this.session.capturedHashes.has(contentHash)) {
      const existingCapture = this.session.capturedHashes.get(contentHash);
      console.log(`Skipping duplicate content (hash: ${contentHash.substring(0, 8)}...) - same content as ${existingCapture.url}`);
      return;
    }
    
    // Check if this URL was captured with different content
    const existingFile = this.session.capturedFiles.get(url);
    if (existingFile && existingFile.contentHash !== contentHash) {
      console.log(`Re-capturing URL with changed content - old hash: ${existingFile.contentHash.substring(0, 8)}..., new hash: ${contentHash.substring(0, 8)}...`);
      // Remove old content hash tracking (memory + persistent store, so a superseded
      // version isn't resurrected on respawn and dedupStore can't grow unbounded).
      this.session.capturedHashes.delete(existingFile.contentHash);
      this.dedupStore.delete(existingFile.contentHash).catch(() => {});
    }

    const fileObject = {
      url: url,
      contentHash: contentHash,
      sessionId: this.session.sessionId,
      tabId: tabId,
      frameId: frameId,
      capturedAt: new Date().toISOString(),
      requestTimestamp: metadata.timestamp,
      statusCode: metadata.statusCode,
      method: metadata.method,
      headers: metadata.headers,
      authContext: metadata.authContext || null,
      contentType: metadata.contentType,
      contentEncoding: contentEncoding,
      contentLength: contentByteLength,
      content: content,
      isMinified: this.isMinified(content),
      classification: classification,
      isThirdParty: thirdParty,
      secretCount: secretCount,
      hasSourceMap: sourceMapData !== null,
      sourceMapUrl: sourceMapUrl,
      sourceMapContent: sourceMapData,
      sourceMapDetection: sourceMapDetection,
      sourceMapFetchStatus: sourceMapFetchStatus,
      sourceMapFetchError: sourceMapFetchError,
      dependencies: dependencies,
      initiator: metadata.initiator,
      documentUrl: metadata.documentUrl,
      needsServerProcessing: sourceMapData !== null || dependencies.length > 0
    };

    // The capture listening NOW owns the file (a respawned outbox may hold an older capture's).
    const runId = this.capture.runIdForEnqueue();
    if (runId) {
      fileObject.runId = runId;
      this.capture.noteBatch();
    }
    // Track both URL and content hash for version-aware deduplication (in-memory).
    this.session.capturedFiles.set(url, fileObject);
    this.updateBadge();
    this.session.capturedHashes.set(contentHash, {url: url, capturedAt: fileObject.capturedAt});

    // Persist the OUTBOX entry BEFORE the durable dedup entry (DEBT D43d): a worker teardown in the
    // gap must never leave a file marked "seen" (dedup) yet never queued for upload — that dropped
    // it permanently. enqueue persists to the outbox first; the in-memory capturedHashes above
    // still guards same-session re-capture regardless of this ordering.
    await this.batchUploader.enqueue(fileObject);
    // Durable pending work now exists — ensure the cold-respawn flush alarm is armed.
    this.reconcileFlushAlarm(true);

    // Persist the dedup entry so a respawn won't re-fetch/re-hash/re-upload this file. Safe after
    // enqueue: the file is already durably queued, so a crash before this line just re-uploads it
    // once on respawn (the server dedupes on session_id, content_hash).
    try { await this.dedupStore.put(contentHash, { contentHash, url, capturedAt: fileObject.capturedAt }); }
    catch (e) { /* dedup is an optimization; a miss just re-uploads (server dedupes) */ }
    // Keep the persisted counter projection in step so a worker teardown can't reset it to 0.
    this.session.schedulePersistCapturedMeta();

    this.session.totalCapturedBytes += contentByteLength;
    this.session.processingStats.processedFiles += 1;

    for (const dep of dependencies) {
      if (dep && dep.type === 'package') {
        continue;
      }
      const depUrl = dep.resolvedUrl || this.resolveUrl(dep.url || dep, url);
      if (!this.isLikelyScriptResource(depUrl)) {
        continue;
      }
      // Dependency children must honour the same denylist / out-of-scope-exclude
      // rules as top-level requests (isInScope is intentionally NOT applied here —
      // dependency resolution may legitimately pull cross-scope libraries).
      if (this.shouldSkipUrl(depUrl, url)) {
        continue;
      }
      if (!this.session.capturedFiles.has(depUrl)) {
        this.session.processingQueue.push({
          metadata: {
            url: depUrl,
            timestamp: new Date().toISOString(),
            initiator: url
          },
          tabId,
          frameId
        });
      }
    }

  }

  extractMetadata(details, authContext = null) {
    const headers = {};
    let contentType = 'application/javascript';
    let contentEncoding = 'identity';

    if (details.responseHeaders) {
      for (const header of details.responseHeaders) {
        const name = header.name.toLowerCase();
        headers[name] = header.value;
        
        if (name === 'content-type') {
          contentType = header.value;
        }
        if (name === 'content-encoding') {
          contentEncoding = header.value;
        }
      }
    }

    return {
      url: details.url,
      timestamp: new Date().toISOString(),
      statusCode: details.statusCode,
      method: details.method,
      type: details.type,
      headers: headers,
      authContext: authContext || null,
      contentType: contentType,
      contentEncoding: contentEncoding,
      initiator: details.initiator,
      documentUrl: details.documentUrl
    };
  }

  needsDecompression(url, encoding, isBinary) {
    const lower = (encoding || '').toLowerCase();
    return (
      !!isBinary ||
      url.endsWith('.gz') ||
      url.endsWith('.br') ||
      url.endsWith('.deflate')
    );
  }

  isMinified(content) {
    if (typeof content !== 'string' || content.length === 0) {
      return false;
    }
    const lines = content.split('\n');
    const avgLineLength = content.length / lines.length;
    const whitespaceRatio = (content.match(/\s/g) || []).length / content.length;
    
    return avgLineLength > 500 || whitespaceRatio < 0.1;
  }

  getContentByteLength(content) {
    if (typeof content === 'string') {
      return new TextEncoder().encode(content).length;
    }
    if (content instanceof ArrayBuffer) {
      return content.byteLength;
    }
    if (ArrayBuffer.isView(content)) {
      return content.byteLength;
    }
    return 0;
  }

  enforceLimits(newBytes) {
    if (newBytes > this.limits.maxFileBytes) {
      this.handleLimitExceeded('File exceeds maximum size limit.');
      return false;
    }
    if (this.session.capturedFiles.size >= this.limits.maxFiles) {
      this.handleLimitExceeded('Maximum file count reached.');
      return false;
    }
    if (this.session.totalCapturedBytes + newBytes > this.limits.maxTotalBytes) {
      this.handleLimitExceeded('Total captured size limit reached.');
      return false;
    }
    return true;
  }

  handleLimitExceeded(message) {
    this.isCapturing = false;
    this.persistCaptureState(false);
    Promise.resolve(chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'Capture Stopped',
      message: message
    })).catch(() => {});
  }

  decodeDataUrl(dataUrl) {
    const match = dataUrl.match(/^data:([^,]*?),(.*)$/);
    if (!match) {
      throw new Error('Invalid data URL');
    }
    const meta = match[1] || '';
    const data = match[2] || '';
    const isBase64 = meta.includes(';base64');
    if (isBase64) {
      return atob(data);
    }
    return decodeURIComponent(data);
  }

  classifySourceMapError(errorMessage) {
    if (!errorMessage) return 'fetch_failed';
    const msg = errorMessage.toLowerCase();
    if (msg.includes('http 404')) return 'not_found';
    if (msg.includes('http 401') || msg.includes('http 403')) return 'forbidden';
    return 'fetch_failed';
  }

  async calculateHash(content) {
    const encoder = new TextEncoder();
    const data = encoder.encode(content);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  }

  resolveUrl(relativeUrl, baseUrl) {
    try {
      return new URL(relativeUrl, baseUrl).href;
    } catch (e) {
      return relativeUrl;
    }
  }

  isLikelyScriptResource(url) {
    if (!url || typeof url !== 'string') return false;
    try {
      const parsed = new URL(url);
      const path = (parsed.pathname || '').toLowerCase();
      if (
        path.endsWith('.js') ||
        path.endsWith('.mjs') ||
        path.endsWith('.cjs') ||
        path.endsWith('.jsx')
      ) {
        return true;
      }
      if (path.includes('/chunk') || path.includes('/chunks/') || path.includes('/bundle')) {
        return true;
      }
      return parsed.search.toLowerCase().includes('.js');
    } catch (e) {
      return false;
    }
  }

  isInScope(url) {
    // The one capture-scope host rule (modules/hook-matches.js), shared with the response-body
    // hook's patterns so the two can never disagree. Fail CLOSED with no scope unless the operator
    // explicitly chose "capture every tab" (which is loudly badged, never a silent default).
    try {
      return hostInScope(this.settings, new URL(url).hostname);
    } catch (e) {
      return false;
    }
  }

  isExtensionRequest(details) {
    return details.initiator &&
           details.initiator.startsWith('chrome-extension://');
  }

  // Record an out-of-scope script host as a discovery hint (D44). Skips the workspace's own origin
  // and denylisted hosts (trackers/CMS noise) so the popup only suggests plausible target hosts.
  // Bounded at 50 distinct hosts so a busy tab can't grow this unboundedly.
  noteOutOfScopeScript(url) {
    try {
      if (this.isWorkspaceUrl(url)) return;
      if (matchesDenylist(url, this.settings.denyRules || [], this.settings.denyDefaultProfile !== false)) return;
      const host = new URL(url).hostname.toLowerCase();
      if (!host) return;
      // Capture lifecycle: a site outside the capture's scope raises the "new site seen" badge.
      if (this.capture.noteSite(host, { captureEverything: this.settings.captureEverything === true })) {
        this.updateBadge();
      }
      if (!this.session.outOfScopeHosts.has(host) && this.session.outOfScopeHosts.size >= 50) return;
      this.session.outOfScopeHosts.set(host, (this.session.outOfScopeHosts.get(host) || 0) + 1);
    } catch (e) { /* ignore malformed URL */ }
  }

  scheduleQueueProcessing() {
    if (this.processingTimer) {
      clearTimeout(this.processingTimer);
    }
    
    this.processingTimer = setTimeout(() => {
      this.processQueue();
    }, 50);
  }

  async loadSettings() {
    const result = await chrome.storage.local.get([
      'domainScopes',
      'useDomainScope',
      'captureEverything',
      'performAnalysisOnUpload',
      'captureSourceMaps',
      'captureResponseBodies',
      'resolveDependencies',
      'isCapturing',
      'captureAuthContext',
      'includeSubdomains',
      'workspaceUrl',
      'authToken',
      'authUser',
      'authTenantName',
      'authTenantId',
      'muteNoise',
      'outOfScopeMode',
      'maxAssetMb',
      'denyDefaultProfile',
      'denyRules'
    ]);

    return {
      domainScopes: result.domainScopes || [],
      useDomainScope: result.useDomainScope || false,
      // Fail-closed default: with no scope AND this off, isInScope captures nothing.
      captureEverything: result.captureEverything === true,
      performAnalysisOnUpload: result.performAnalysisOnUpload === true,
      captureSourceMaps: result.captureSourceMaps !== false,
      // Response-body capture is OPT-IN (posture: it collects response DATA) — default OFF (D45b2).
      captureResponseBodies: result.captureResponseBodies === true,
      resolveDependencies: result.resolveDependencies !== false,
      isCapturing: result.isCapturing || false,
      captureAuthContext: result.captureAuthContext !== false,
      // --- redesigned popup settings ---
      // includeSubdomains MUST default true to preserve today's always-match
      // subdomain capture behaviour (isInScope) for existing users.
      includeSubdomains: result.includeSubdomains !== false,
      workspaceUrl: result.workspaceUrl || '',
      // Central-login session token (recon.auth) + cached identity for the popup. Empty =>
      // not signed in (unauthenticated ingest => shared capture tenant); a valid token routes
      // captures to the operator's tenant (the upload Bearer, see setAuthToken).
      authToken: result.authToken || '',
      authUser: result.authUser || '',
      authTenantName: result.authTenantName || '',
      authTenantId: result.authTenantId || '',
      muteNoise: result.muteNoise !== false,
      outOfScopeMode: result.outOfScopeMode || 'tag',
      // Default 10 MB to match the backend ceiling (settings.max_upload_bytes) and the
      // server-advertised capture config (maxAssetMb: 10); an 8 MB default silently skipped
      // 8-10 MB main bundles (DEBT D43b). Clamp to 10 so a legacy stored value (from the old
      // 25 MB slider) can't wave through files the server will 422.
      maxAssetMb: Math.min(10, typeof result.maxAssetMb === 'number' ? result.maxAssetMb : 10),
      denyDefaultProfile: result.denyDefaultProfile !== false,
      denyRules: Array.isArray(result.denyRules) ? result.denyRules : DEFAULT_DENY_RULES
    };
  }

  async fetchViaContentScript(tabId, frameId, url) {
    try {
      const response = await chrome.tabs.sendMessage(
        tabId,
        { action: 'fetchUrl', url },
        { frameId }
      );
      if (response && response.success) {
        return {
          success: true,
          content: response.content,
          contentEncoding: 'identity'
        };
      }
      return { success: false, error: response?.error || 'Content script fetch failed' };
    } catch (error) {
      return { success: false, error: error.message };
    }
  }

  // The capture gate for the capture lifecycle: the scope the gate matches (settings.domainScopes,
  // read by hook-matches/isInScope) and whether it listens. Only the worker writes it.
  async applyCaptureGate({ scope, capturing }) {
    const domainScopes = Array.isArray(scope) ? scope : [];
    this.settings = {
      ...this.settings,
      domainScopes,
      useDomainScope: domainScopes.length > 0,
      includeSubdomains: true,
    };
    await chrome.storage.local.set({ domainScopes, useDomainScope: domainScopes.length > 0, includeSubdomains: true });
    this.batchUploader.setScope(domainScopes.length ? { rootDomains: domainScopes, includeSubdomains: true } : null);
    this.isCapturing = !!capturing;
    await this.persistCaptureState(this.isCapturing);
    this.syncResponseBodyHook();
    this.updateBadge();
  }

  replyFrom(res) {
    return res && res.ok ? { ok: true, ...(res.data || {}) } : { ok: false, status: res && res.status, error: res && res.error };
  }

  async persistCaptureState(isCapturing) {
    try {
      await chrome.storage.local.set({ isCapturing });
      await publishCaptureState(chrome.storage, isCapturing); // what content scripts read
    } catch (e) {
      console.warn('Failed to persist capture state', e);
    }
  }

  handleMessage(request, sender, sendResponse) {
    const handlers = {
      startCapture: () => this.startCapture(sendResponse),
      // Capture lifecycle (one capture = one platform run).
      captureLookup: async (req) => sendResponse(this.replyFrom(await this.capture.lookup(req.scope))),
      captureStart: async (req) => sendResponse(await this.capture.start({
        scope: req.scope, mode: req.mode, match: req.match,
        binding: { projectId: req.projectId || null, captureConfig: {}, overrideKeys: [] },
      })),
      capturePause: async () => sendResponse(await this.capture.pause()),
      captureResume: async () => sendResponse(await this.capture.resume()),
      captureEnd: async () => sendResponse(await this.capture.end()),
      captureAddScope: async (req) => {
        const res = await this.capture.addToScope(req.host);
        this.updateBadge();
        sendResponse(res);
      },
      captureDismissSite: async (req) => {
        const res = await this.capture.dismissSite(req.site);
        this.updateBadge();
        sendResponse(res);
      },
      newSession: (req) => this.newSession(req, sendResponse),
      stopCapture: () => this.stopCapture(sendResponse),
      getFiles: () => this.getFiles(sendResponse),
      clearFiles: async () => { await this.clearFiles(sendResponse); },
      getStatus: () => this.getStatus(sendResponse),
      updateSettings: (req) => this.updateSettings(req, sendResponse),
      getExportData: (req) => this.getExportData(req, sendResponse),
      testConnection: async () => sendResponse(await this.workspaceClient.testConnection()),
      analyzeSession: async () => sendResponse(await this.workspaceClient.analyzeSession()),
      getAnalysisProgress: async () => sendResponse(await this.workspaceClient.getAnalysisProgress()),
      getSessionFindingsSummary: async (req) => {
        const result = await this.workspaceClient.getSessionFindingsSummary(req.sessionId);
        // D46(c): cache the summary so history can persist it before rotation.
        this.session.noteFindingsSummary(req.sessionId, result);
        sendResponse(result);
      },
      getHistory: async () => { await this.getHistory(sendResponse); },
      listProjects: () => this.listProjects(sendResponse),
      createProject: async (req) => sendResponse(await this.workspaceClient.createProject(req.project)),
      login: (req) => this.login(req, sendResponse),
      logout: () => this.logout(sendResponse),
      dynamicScriptDetected: (req) => this.handleDynamicScript(req, sender),
      inlineScriptDetected: (req) => this.handleInlineScript(req, sender),
      responseBodyObserved: (req) => this.handleResponseBody(req),
      saveLlmConfig: async (req) => sendResponse(await this.workspaceClient.saveLlmConfig(req.config || {})),
      testLlmConfig: async () => sendResponse(await this.workspaceClient.testLlmConfig())
    };

    const handler = handlers[request.action];
    // Returned so dispatchReply can await it (and answer a rejection). An unknown action is
    // answered too — the popup must never wait on a message no handler will reply to.
    if (!handler) return sendResponse({ ok: false, error: 'unknown_action' });
    return handler(request);
  }

  handleDynamicScript(request, sender) {
    if (!this.isCapturing) return;
    if (!request || !request.url) return;
    if (!this.isInScope(request.url)) return;
    if (this.shouldSkipUrl(request.url, sender?.tab?.url || request.documentUrl)) return;

    const senderTabId = sender?.tab?.id;
    const senderFrameId = sender?.frameId;
    const tabId = Number.isInteger(senderTabId) ? senderTabId : -1;
    const frameId = Number.isInteger(senderFrameId) ? senderFrameId : 0;

    this.session.processingQueue.push({
      metadata: {
        url: request.url,
        timestamp: request.timestamp || new Date().toISOString(),
        initiator: request.initiator || 'dynamic-script',
        method: 'GET'
      },
      tabId: tabId,
      frameId: frameId
    });

    this.scheduleQueueProcessing();
  }

  // An inline <script> body the content script read from the DOM (DEBT D45a). webRequest never
  // sees inline scripts (they make no request) and the URL-keyed noise gate can't classify one
  // (its synthetic URL is the in-scope page), so scope is applied on the PAGE url and a
  // CONTENT-based relevance filter drops analytics/hydration noise (review Claim 3).
  handleInlineScript(request, sender) {
    if (!this.isCapturing) return;
    if (!request || typeof request.content !== 'string') return;
    const pageUrl = request.pageUrl || sender?.tab?.url;
    if (!pageUrl) return;
    if (!this.isInScope(pageUrl)) { this.noteOutOfScopeScript(pageUrl); return; }
    if (this.shouldSkipUrl(pageUrl, pageUrl)) return;
    if (!isRelevantInlineScript(request.content)) return;

    // Per-page cap so a churny SPA can't flood the outbox (review Claim 4).
    const origin = this.originOf(pageUrl);
    const seen = this.session.inlinePerPage.get(origin) || 0;
    if (seen >= INLINE_PER_PAGE_CAP) return;
    this.session.inlinePerPage.set(origin, seen + 1);

    // Synthetic URL keyed on the reported ordinal, not content hash. A full re-scan (reload/bfcache)
    // reuses document-order ordinals, so a changed inline block at the same slot SUPERSEDES via
    // processFile's changed-content branch. A mutation-added block (SPA soft-nav) gets a monotonic
    // ordinal and instead accumulates — bounded by INLINE_PER_PAGE_CAP + content-hash dedup + the
    // relevance filter (which drops the dominant __next_f/hydration flood), not by supersede.
    const ordinal = Number.isInteger(request.ordinal) ? request.ordinal : seen;
    const syntheticUrl = `${pageUrl}#inline-${ordinal}`;

    const senderTabId = sender?.tab?.id;
    const senderFrameId = sender?.frameId;
    const tabId = Number.isInteger(senderTabId) ? senderTabId : -1;
    const frameId = Number.isInteger(senderFrameId) ? senderFrameId : 0;

    this.session.processingQueue.push({
      metadata: {
        url: syntheticUrl,
        timestamp: request.timestamp || new Date().toISOString(),
        initiator: pageUrl,
        documentUrl: pageUrl,
        method: 'GET',
        contentType: 'application/javascript',
        // Signals processFile to use this content directly (no network fetch — the synthetic
        // URL isn't fetchable) and to skip the source-map paths (inline carries no map ref).
        inlineContent: request.content
      },
      tabId: tabId,
      frameId: frameId
    });

    this.scheduleQueueProcessing();
  }

  originOf(url) {
    try { return new URL(url).origin; } catch (e) { return url || ''; }
  }

  // Record one runtime API call as a { method, url } observation (DEBT D45b1) for endpoint
  // confirmation. NO body is captured here (that's the opt-in main-world path). Scope is enforced
  // HERE because the platform's ingest scope is inert for pre-fetched captures, so this gate is
  // the no-noise boundary for observations.
  recordObservation(details) {
    // Reclaim + delete the pending request body FIRST — before any early-return — so a body never
    // orphans when we bail below (capture toggled off mid-flight, extension request, out of scope).
    // A completed request always hits onCompleted, so this is the reliable cleanup point (review
    // Finding 3).
    const reqBody = this.session.pendingRequestBodies.get(details.requestId);
    if (reqBody !== undefined) this.session.pendingRequestBodies.delete(details.requestId);
    if (!this.isCapturing) return;
    if (this.isExtensionRequest(details)) return;

    const rawUrl = details.url;
    if (!this.isInScope(rawUrl)) return;
    if (this.shouldSkipUrl(rawUrl, details.documentUrl)) return;
    if (isTelemetryPath(rawUrl)) return;
    if (!isApiIshObservation(this.responseContentType(details.responseHeaders))) return;

    const url = normalizeObservedUrl(rawUrl);
    if (!url) return;
    const method = (details.method || 'GET').toUpperCase();
    const key = method + ' ' + url;
    if (this.session.observationKeys.has(key)) return;
    if (this.session.observationKeys.size >= OBSERVATION_CAP) return;
    this.session.observationKeys.add(key);

    const obs = { method, url };
    // Attach the redacted request body if we captured one and we're under the total-body budget.
    if (reqBody && this.session.bodyBytesUsed < BODY_TOTAL_CAP) {
      obs.reqBody = reqBody;
      this.session.bodyBytesUsed += reqBody.length;
    }
    this.session.observations.push(obs);
    // Eager persist every 50 observations so the loss window on an MV3 teardown is bounded
    // to at most 50 entries regardless of the debounce timer state (Fix 8).
    if (this.session.observations.length % 50 === 0) {
      this.session.persistObservations();
    } else {
      this.session.schedulePersistObservations();
    }
  }

  // Capture an in-scope XHR/fetch REQUEST body (DEBT D45b2, on by default), keyed by requestId for
  // recordObservation to attach. Redacted + capped before storage; gated by the same scope/denylist
  // as observations, plus the per-session total-body budget.
  captureRequestBody(details) {
    if (!this.isCapturing) return;
    if (this.isExtensionRequest(details)) return;
    if (!details.requestBody) return;
    const url = details.url;
    if (!this.isInScope(url)) return;
    if (this.shouldSkipUrl(url, details.documentUrl)) return;
    if (this.session.bodyBytesUsed >= BODY_TOTAL_CAP) return;
    if (this.session.pendingRequestBodies.size >= 512) return; // bound un-completed requests
    const prepared = prepareRequestBody(details.requestBody, REQ_BODY_CAP);
    if (!prepared || !prepared.text) return;
    this.session.pendingRequestBodies.set(details.requestId, prepared.text);
  }

  // A RESPONSE body from the opt-in main-world hook (DEBT D45b2). Lower-trust (page-forgeable) —
  // re-apply scope + denylist + telemetry + budget here (the hook can't see scope), redact, cap,
  // and attach to the matching observation (dedup keeps one respBody per method+url).
  handleResponseBody(request) {
    if (!this.isCapturing) return;
    if (!this.settings || this.settings.captureResponseBodies !== true) return;
    if (!request || typeof request.url !== 'string' || typeof request.body !== 'string') return;
    const rawUrl = request.url;
    if (!this.isInScope(rawUrl)) return;
    if (this.shouldSkipUrl(rawUrl, request.pageUrl)) return;
    if (isTelemetryPath(rawUrl)) return;
    // Apply the same API-ish content-type gate as recordObservation — the forgeable hook's own
    // filter is not trusted in the worker (review nit).
    if (!isApiIshObservation(request.contentType)) return;
    if (this.session.bodyBytesUsed >= BODY_TOTAL_CAP) return;

    const url = normalizeObservedUrl(rawUrl);
    if (!url) return;
    const method = (request.method || 'GET').toUpperCase();
    const key = method + ' ' + url;
    const respBody = capBody(redactBody(request.body), RESP_BODY_CAP);
    if (!respBody) return;

    let obs = null;
    if (this.session.observationKeys.has(key)) {
      for (let i = this.session.observations.length - 1; i >= 0; i--) {
        const o = this.session.observations[i];
        if (o.method + ' ' + o.url === key) { obs = o; break; }
      }
    }
    if (!obs) {
      if (this.session.observationKeys.size >= OBSERVATION_CAP) return;
      this.session.observationKeys.add(key);
      obs = { method, url };
      this.session.observations.push(obs);
    }
    if (obs.respBody) return; // keep the first response body for this endpoint
    obs.respBody = respBody;
    this.session.bodyBytesUsed += respBody.length;
    this.session.schedulePersistObservations();
  }

  // Register/unregister the opt-in main-world response-body hook (DEBT D45b2). A statically-declared
  // content script can't be toggled, so it's registered dynamically ONLY while captureResponseBodies
  // is on — the default build ships zero main-world code — and ONLY on the in-scope hosts
  // (modules/hook-matches.js): it patches the page's own fetch/XHR, so it must never run on a site
  // capture would ignore. Re-synced whenever the setting or the scope changes; an existing
  // registration with other patterns (e.g. an older build's <all_urls>) is updated in place.
  // Syncs run ONE AT A TIME, each recomputing from the settings current when it runs: the scope
  // box fires an update per keystroke, and overlapping read-then-register/update/unregister steps
  // raced ("Duplicate script ID" / updating an unregistered script), leaving the hook off for an
  // in-scope host or on for a half-typed one.
  syncResponseBodyHook() {
    this.hookSync = (this.hookSync || Promise.resolve()).then(() => this.applyResponseBodyHook());
    return this.hookSync;
  }

  async applyResponseBodyHook() {
    const matches = hookMatchPatterns(this.settings);
    try {
      if (!chrome.scripting || !chrome.scripting.getRegisteredContentScripts) return;
      const existing = await chrome.scripting.getRegisteredContentScripts({ ids: ['recon-xhr-hook'] });
      const current = Array.isArray(existing) && existing.length > 0 ? existing[0] : null;
      if (matches.length === 0) {
        if (current) await chrome.scripting.unregisterContentScripts({ ids: ['recon-xhr-hook'] });
        return;
      }
      // Order-insensitive: Chrome doesn't promise to return patterns in the order registered.
      const same = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
      if (current && same(current.matches || [], matches)) return;
      if (current) {
        await chrome.scripting.updateContentScripts([{ id: 'recon-xhr-hook', matches }]);
        return;
      }
      await chrome.scripting.registerContentScripts([{
        id: 'recon-xhr-hook',
        matches,
        js: ['inject/xhr-hook.js'],
        runAt: 'document_start',
        world: 'MAIN',
        allFrames: true
      }]);
    } catch (e) {
      // Non-fatal (the feature stays as it was), but never silent: a lost sync means the hook
      // runs on the wrong hosts until the next settings change or worker start.
      console.warn('response-body hook sync failed', e);
    }
  }

  responseContentType(responseHeaders) {
    if (!Array.isArray(responseHeaders)) return '';
    for (const h of responseHeaders) {
      if (h && typeof h.name === 'string' && h.name.toLowerCase() === 'content-type') {
        return h.value || '';
      }
    }
    return '';
  }

  // Capture just turned on: pull in the JS the ACTIVE tab already loaded. webRequest only sees
  // NEW script requests and nothing else re-reads a loaded page, so without this an already-open
  // tab captures nothing until the operator reloads. Best-effort + fire-and-forget — a tab with
  // no content script (chrome://, the web store, a blank tab) just rejects the message.
  async rescanActiveTab() {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && Number.isInteger(tab.id)) {
        const sent = chrome.tabs.sendMessage(tab.id, { action: 'rescanScripts' });
        if (sent && typeof sent.catch === 'function') sent.catch(() => {});
      }
    } catch (e) {
      // tabs API unavailable / no active tab — non-fatal.
    }
  }

  startCapture(sendResponse) {
    this.isCapturing = true;
    this.persistCaptureState(true);
    this.updateBadge();
    this.rescanActiveTab();
    sendResponse({ success: true, sessionId: this.session.sessionId });
  }

  // Toolbar badge (D46): show live capture state while the popup is closed. Empty when paused; the
  // captured-file count while capturing, orange when delivery/processing is unhealthy (else lime).
  updateBadge() {
    try {
      if (!chrome.action || !chrome.action.setBadgeText) return;
      const newSites = (this.capture.state && this.capture.state.newSites.length) || 0;
      const n = this.session.capturedFiles.size;
      if (!this.isCapturing) { chrome.action.setBadgeText({ text: badgeText({ capturing: false, files: n, newSites }) }); return; }
      const up = this.batchUploader.getStats();
      const bad = !!up.lastError || (up.droppedFiles || 0) > 0 || up.paired === false ||
        up.authPaused === true || (this.session.processingStats.failedFiles || 0) > 0;
      chrome.action.setBadgeText({ text: badgeText({ capturing: true, files: n, newSites }) });
      chrome.action.setBadgeBackgroundColor({ color: bad ? '#ff8a47' : '#4ea86b' });
    } catch (e) { /* action API unavailable in this context */ }
  }

  // Auth token expired/rejected mid-capture (DEBT D41). The uploader has already re-queued the
  // batch and paused the drain (authPaused, surfaced via getStatus so the popup shows a "session
  // expired — sign in again" banner). We KEEP capturing so nothing is lost: new files buffer to the
  // durable outbox and flush on re-auth. Fire ONE notification per episode (authNotified guard) so
  // repeated 401s don't spam; refresh the badge to the unhealthy colour.
  handleAuthExpired(status) {
    this.updateBadge();
    if (this.authNotified) return;
    this.authNotified = true;
    try {
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icons/icon48.png',
        title: 'Session expired',
        message: `Sign in again to keep uploading captures (auth ${status || 'expired'}). Capture continues; nothing is lost.`
      });
    } catch (e) { /* notifications API unavailable in this context */ }
  }

  // A token that expired while the worker slept gets ONE "session expired" notification, not one per
  // respawn (the in-memory authNotified guard dies with the worker) and not none (the up-front
  // pause sends no doomed upload, so no 401 would ever fire it). Keyed by the token's exp in
  // storage.session, so a new token that later expires notifies again.
  async notifyExpiredOncePerToken() {
    const exp = tokenExpiry(this.settings.authToken);
    try {
      const { authNotifiedExp } = await chrome.storage.session.get('authNotifiedExp');
      if (authNotifiedExp === exp) return;
      await chrome.storage.session.set({ authNotifiedExp: exp });
    } catch (e) { /* storage.session unavailable: notify rather than stay silent */ }
    this.handleAuthExpired('expired');
  }

  // Central login: authenticate to the workspace and persist the session token + identity so
  // uploads/analyze route to this operator's tenant. The Bearer comes from a password login
  // (POST /auth/login).
  async login(request, sendResponse) {
    const { username, password } = request || {};
    try {
      const result = await this.workspaceClient.login(username, password);
      if (result && result.success) {
        const prevTenantId = this.settings.authTenantId || '';
        const nextTenantId = (result.tenant && result.tenant.id) || '';
        // Signing in as a DIFFERENT tenant must not stamp/flush the previous tenant's captures
        // under the new tenant's token. The server binding is immutable per session, so the only
        // correct unbind is a fresh Standalone session. Do this reset BEFORE installing the new
        // token: resetCaptureSession awaits storage I/O (it yields the event loop), so if the new
        // token were already live a timer / alarm / in-flight-retry flush could send tenant-A's
        // queued files under tenant B. Clearing the token first means any stray flush during the
        // reset goes out unauthenticated (shared tenant), never as another named tenant; clearOutbox
        // (epoch-bumped) then drops A's files, including any in-flight batch that later re-queues.
        // Same-tenant re-login (token refresh) is left intact.
        if (prevTenantId && nextTenantId && prevTenantId !== nextTenantId) {
          this.batchUploader.setAuthToken('');
          await this.resetCaptureSession({ stopCapturing: false, dropOutbox: true });
        }
        Object.assign(this.settings, {
          authToken: result.token || '',
          authUser: result.user || username || '',
          authTenantName: (result.tenant && result.tenant.name) || '',
          authTenantId: nextTenantId,
          // A token that reads as expired the moment it's issued means this machine's clock is
          // off: stop trusting the local expiry read, or every popup open would demand sign-in.
          localExpiryUntrusted: authMarker(result.token).authExpired
        });
        await chrome.storage.local.set(this.settings);
        this.batchUploader.setAuthToken(this.settings.authToken);
        // Re-auth: lift any auth-expiry pause and drain the buffered outbox under the fresh token
        // (DEBT D41). Unconditional by design — tokens can repeat within a second, so the resume
        // must NOT be gated on a token change. Re-arm the notification for a future expiry.
        this.batchUploader.resumeUploads();
        this.capture.resumeAfterLogin();
        this.authNotified = false;
        sendResponse({ success: true, user: this.settings.authUser, tenant: result.tenant || null, role: result.role || '' });
        return;
      }
      sendResponse(result || { success: false, error: 'login failed' });
    } catch (e) {
      // Always respond so the popup's message port never leaks (e.g. storage.set rejects).
      sendResponse({ success: false, error: e?.message || 'login failed' });
    }
  }

  // Clear the session token + identity; captures revert to unauthenticated (rejected by a
  // fail-closed backend, or routed to the shared tenant when anon capture is allowed).
  async logout(sendResponse) {
    // Clear the in-memory token FIRST so uploads stop routing even if the persist fails;
    // then best-effort persist so a respawn doesn't rehydrate the old token. authTenantId is
    // deliberately KEPT so a subsequent login as a DIFFERENT tenant is still detected (and its
    // outbox dropped) — logging back into the SAME tenant then resumes any pending outbox.
    Object.assign(this.settings, { authToken: '', authUser: '', authTenantName: '' });
    this.batchUploader.setAuthToken('');
    try {
      await chrome.storage.local.set(this.settings);
    } catch (e) {
      // Persist is best-effort; the in-memory clear already took effect.
    }
    // Stop capturing and reset to a fresh Standalone session: capture must not keep running
    // behind the sign-in gate, and the next sign-in must start clean (no old-tenant binding). The
    // outbox is kept (it cannot leak without a token; a same-tenant re-login resumes it).
    await this.resetCaptureSession({ stopCapturing: true });
    sendResponse({ success: true });
  }

  // Rotate to a fresh, UNBOUND (Standalone) capture session and drop the previous session's
  // captured state + engagement binding. Used on logout and on a tenant-changing login. The
  // server binds engagement immutably at session-create (keyed by external_id), so clearing the
  // uploader config alone is not enough — a new session id is the only correct "unbind". Mirrors
  // the state-clearing half of newSession; the outbox is intentionally left intact (unsent files
  // keep their own old per-file session id).
  async resetCaptureSession({ stopCapturing = false, dropOutbox = false } = {}) {
    // One tenant's capture never continues under another's token: forget it here (the platform's
    // 24 h idle sweeper ends it server-side).
    await this.capture.forget();
    if (stopCapturing) {
      this.isCapturing = false;
      this.persistCaptureState(false);
    }
    await this.session.startStandalone({ dropOutbox });
    this.authNotified = false;
  }

  async newSession(request, sendResponse) {
    // Map the client-resolved effective config. The popup resolved (project.defaults +
    // per-session overrides) and sent the snapshot; here we map it onto the flat capture-gate
    // keys and the uploader. A blank/absent captureConfig leaves the non-scope gate as-is
    // (back-compat with pre-project popups); a blank scope RESETS gating (settingsFromConfig
    // emits domainScopes=[] / useDomainScope=false) so a new session can't silently inherit the
    // previous session's domainScopes.
    const req = request || {};
    const reqScope = req.scope || {};
    const rootDomains = normalizeRootDomains(reqScope.rootDomains);
    const includeSubdomains = reqScope.includeSubdomains !== false;
    const captureConfig = (req.captureConfig && typeof req.captureConfig === 'object') ? req.captureConfig : {};
    const overrideKeys = Array.isArray(req.overrideKeys) ? req.overrideKeys : [];
    const projectId = req.projectId || null;
    const patch = settingsFromConfig({ scope: { rootDomains, includeSubdomains }, ...captureConfig });

    // Flush + save history under the old id, rotate, clear, and bind the new engagement. The
    // flat gate keys go in the same storage write as the binding, so a respawn sees both or
    // neither. The in-memory gate switches in the same turn as the rotation (onRotated): history
    // was saved with the OLD settings, and no request may see the new id with the old scope.
    await this.session.startNew({
      binding: { projectId, captureConfig, overrideKeys },
      persistWith: patch,
      onRotated: () => Object.assign(this.settings, patch)
    });
    this.authNotified = false;
    this.batchUploader.setScope({ rootDomains, includeSubdomains });
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    // The new session's scope (and capture config) may move the response-body hook's hosts.
    await this.syncResponseBodyHook();

    // A fresh session on an already-loaded page should still capture that page (parity with
    // startCapture) — webRequest won't refire for JS that loaded before the rotation.
    if (this.isCapturing) this.rescanActiveTab();

    sendResponse({
      success: true,
      sessionId: this.session.sessionId,
      scope: { rootDomains, includeSubdomains },
      projectId,
      overrideKeys
    });
  }

  async stopCapture(sendResponse) {
    this.isCapturing = false;
    // Drop request bodies captured for still-in-flight requests so a stop/start can't strand them
    // in the pending map (review Finding 3); the completed-request path also self-cleans up-front.
    this.session.pendingRequestBodies.clear();
    this.persistCaptureState(false);
    this.updateBadge();
    // Answer first: the flush can take minutes on a slow workspace, and the popup polls getStatus
    // for the drain anyway (C7 — the worker owns deadlines; the popup never waits on a flush).
    sendResponse({
      success: true,
      fileCount: this.session.capturedFiles.size,
      uploader: this.batchUploader.getStats()
    });
    await this.batchUploader.flushAll();
  }

  getFiles(sendResponse) {
    const files = Array.from(this.session.capturedFiles.values()).map(f => ({
      url: f.url,
      size: f.contentLength,
      hasSourceMap: f.hasSourceMap,
      dependencyCount: Array.isArray(f.dependencies) ? f.dependencies.length : (f.dependencyCount || 0),
      capturedAt: f.capturedAt,
      isMinified: f.isMinified,
      classification: f.classification || 'app',
      isThirdParty: f.isThirdParty === true,
      secretCount: f.secretCount || 0,
      sourceMapFetchStatus: f.sourceMapFetchStatus,
      sourceMapFetchError: f.sourceMapFetchError
    }));
    
    sendResponse({
      files,
      total: files.length,
      sessionId: this.session.sessionId,
      isCapturing: this.isCapturing
    });
  }

  async clearFiles(sendResponse) {
    await this.session.clearCaptured();
    this.authNotified = false;
    this.updateBadge();
    sendResponse({ success: true });
  }

  getStatus(sendResponse) {
    let mapsCount = 0;
    let secretCount = 0;
    for (const f of this.session.capturedFiles.values()) {
      if (f.hasSourceMap) mapsCount += 1;
      secretCount += f.secretCount || 0;
    }
    // Surface the active engagement so the popup can show + restore it on every open. The
    // projectId is the uploader's live in-memory binding (restored on cold start via
    // pendingSessionConfig, initialize()), so this stays synchronous — no storage await.
    const uploaderStats = this.batchUploader.getStats();
    // Replace the raw Bearer token with a truthy-only marker: the popup gates on
    // settings.authToken being truthy (line 378 app.jsx) but never uses the value.
    // 'session' matches the marker the popup sets locally on login (app.jsx:207).
    // A token known to be expired reads as signed out, so the popup asks for sign-in BEFORE a
    // capture starts rather than after its first upload 401s. authExpired is computed here only,
    // never stored in settings (settings are adopted once by the popup and persisted whole).
    const { authToken, ...restSettings } = this.settings || {};
    const marker = authMarker(authToken, { trustLocalExpiry: !this.settings?.localExpiryUntrusted });
    const safeSettings = { ...restSettings, authToken: marker.authToken };
    sendResponse({
      isCapturing: this.isCapturing,
      authExpired: marker.authExpired,
      sessionId: this.session.sessionId,
      fileCount: this.session.capturedFiles.size,
      mapsCount,
      secretCount,
      queueLength: this.session.processingQueue.length,
      processingStats: this.session.processingStats,
      outOfScopeHosts: [...this.session.outOfScopeHosts.entries()].map(([host, count]) => ({ host, count })),
      uploader: uploaderStats,
      projectId: uploaderStats.projectId || null,
      standalone: !uploaderStats.projectId,
      // The capture in progress (or the last one ended), owned by the worker; the popup reads it.
      ...this.capture.snapshot(),
      settings: safeSettings
    });
  }

  async updateSettings(request, sendResponse) {
    const incoming = { ...(request.settings || {}) };
    // Scope entries drive the capture gate (isInScope reads this.settings.domainScopes directly),
    // so normalize them at the WRITE here too — not just on the newSession path (applyConfig). Without
    // this, a `*.target.com` / `https://target.com/` typed in the Settings box (or the one-tap arm)
    // is stored literally and matches no host = silent no-op capture (D40).
    if (Array.isArray(incoming.domainScopes)) {
      incoming.domainScopes = normalizeRootDomains(incoming.domainScopes);
    }
    this.settings = { ...this.settings, ...incoming };
    if (typeof this.settings.captureAuthContext !== 'boolean') {
      this.settings.captureAuthContext = true;
    }
    await chrome.storage.local.set(this.settings);
    // Toggle the opt-in main-world response-body hook to match the new setting (DEBT D45b2).
    this.syncResponseBodyHook();
    this.batchUploader.setEndpoint(this.workspaceClient.resolveApiBase());
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    // Push a changed login token to the uploader (workspace-client reads it live via
    // getSettings, so it needs no push). A cleared token reverts to shared-tenant ingest.
    this.batchUploader.setAuthToken(this.settings.authToken);
    sendResponse({ success: true });
  }

  async listProjects(sendResponse) {
    // Live list refreshes the cache; a workspace blip falls back to the cached list so the
    // popup's engagement picker still renders. Never throws.
    // The popup calls this on every open, so its 401 doubles as the server's verdict on the session
    // token (the same tenant check uploads get). Only for the token still held: a reply that lands
    // after a fresh sign-in must not bounce the operator back to the sign-in screen.
    const tokenAtSend = this.settings?.authToken;
    const { projects, source, status } = await listProjectsWithCache(
      () => this.workspaceClient.listProjects(),
      chrome.storage.local
    );
    sendResponse({
      success: true, projects, source,
      authRejected: status === 401 && !!tokenAtSend && this.settings?.authToken === tokenAtSend
    });
  }

  getExportData(request, sendResponse) {
    const files = Array.from(this.session.capturedFiles.values());
    // D46: let the operator export WITH the captured code, not just metadata. Best-effort — files
    // rehydrated after a worker respawn are lean (no content) and simply export without it.
    const includeContent = !!(request && request.includeContent);

    try {
      const exportData = buildExportData({
        sessionId: this.session.sessionId,
        files,
        includeContent,
        version: '3.0.0'
      });

      sendResponse({
        success: true,
        filename: `js-extraction-${this.session.sessionId}${includeContent ? '-with-code' : ''}.json`,
        exportData
      });
    } catch (error) {
      console.error('Export payload build failed:', error);
      sendResponse({ success: false, error: error.message });
    }
  }
}

const extractor = new JSExtractor();
// Kick off async init and expose the promise so the listeners (registered synchronously
// below) can gate their handlers on it. .catch keeps `ready` resolvable even if init
// fails, so handlers proceed with best-effort state instead of hanging forever.
extractor.ready = extractor.initialize().catch((e) => console.error('JSExtractor init failed:', e));
// Register listeners SYNCHRONOUSLY in the worker's first turn (MV3 requirement) so the
// event that woke the worker — a page's first script request or the flushOutbox alarm —
// is actually routed to us instead of being dropped.
extractor.setupListeners();
