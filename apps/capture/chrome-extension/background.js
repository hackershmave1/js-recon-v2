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

// Seed denylist shown in the redesigned popup Settings on first run.
const DEFAULT_DENY_RULES = [
  { tag: 'CMS', pattern: '/wp-content/plugins/*' },
  { tag: 'CMS', pattern: '/wp-includes/*' },
  { tag: 'TRACK', pattern: '*.google-analytics.com' },
  { tag: 'TRACK', pattern: '*.doubleclick.net' },
  { tag: 'LIB', pattern: '*/jquery*.min.js' }
];

// Reduce user-supplied scope entries (full URL, host:port, user@host, www.host) to bare
// hostnames so they gate capture (domainScopes) and match the backend's normalize_root_domains.
function normalizeRootDomains(values) {
  const list = Array.isArray(values) ? values : [];
  const out = [];
  for (const value of list) {
    let host = String(value || '').trim().toLowerCase();
    if (!host) continue;
    host = host.replace(/^[a-z][a-z0-9+.-]*:\/\//, ''); // scheme
    host = host.replace(/^[^@/]*@/, '');                // userinfo
    host = host.split('/')[0].split('?')[0].split('#')[0].split(':')[0]; // path/query/frag/port
    if (host.startsWith('www.')) host = host.slice(4);
    if (host && !out.includes(host)) out.push(host);
  }
  return out;
}

// Messages the popup/content-script send without waiting for a response. The onMessage
// listener must NOT hold the response channel open for these (see setupListeners).
const FIRE_AND_FORGET_ACTIONS = new Set(['dynamicScriptDetected']);

class JSExtractor {
  constructor() {
    this.capturedFiles = new Map(); // url -> fileObject (for export/display)
    this.capturedHashes = new Map(); // hash -> {url, capturedAt} (for deduplication)
    this.processingQueue = [];
    this.isCapturing = false;
    this.settings = null;
    this.sessionStore = new SessionStore();
    // Placeholder id for the brief window before initialize() restores the persisted
    // one; overwritten in initialize() before any capture/message listener attaches.
    this.sessionId = this.sessionStore.generate();
    this.totalCapturedBytes = 0;
    this.processingStats = {
      processedFiles: 0,
      failedFiles: 0,
      lastFailureReason: null,
      lastFailureUrl: null,
      lastFailureMessage: null
    };

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
      getSessionId: () => this.sessionId,
      batchUploader: this.batchUploader
    });
    // Durable stores (IndexedDB) that outlive the service worker: the upload outbox
    // (unsent files) and the dedup set (hash -> {url, capturedAt}). Separate DBs to
    // avoid multi-store upgrade coordination.
    this.outboxStore = new IdbStore('recon-outbox');
    this.dedupStore = new IdbStore('recon-dedup');
    // Tracks whether the durable-flush alarm is armed, so per-file reconcile calls
    // don't re-create it on every capture.
    this.flushAlarmArmed = false;
    // Resolves when initialize() completes; the bootstrap replaces this with the real
    // init promise. Listener handlers await it so a cold-woken worker finishes loading
    // settings/session/stores before handling the event that woke it.
    this.ready = Promise.resolve();
  }

  buildProcessingError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  recordProcessingFailure(reason, url, message) {
    this.processingStats.failedFiles += 1;
    this.processingStats.lastFailureReason = reason;
    this.processingStats.lastFailureUrl = url || null;
    this.processingStats.lastFailureMessage = message || null;
  }

  async initialize() {
    this.settings = await this.loadSettings();
    // Restore the persisted session id (or mint one on first run) so a service-worker
    // respawn resumes the SAME backend session instead of fragmenting into a new one.
    this.sessionId = await this.sessionStore.loadOrCreate();
    this.batchUploader.setEndpoint(this.workspaceClient.resolveApiBase());
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    // Re-apply the persisted pairing token so a service-worker respawn keeps routing the
    // operator's captures into their tenant (the uploader holds it in memory, not storage).
    this.batchUploader.setAuthToken(this.settings.pairingToken);
    // Re-apply a persisted scope so uploads keep tagging the session even if the
    // service worker recycled after a new session was started.
    if (this.settings.useDomainScope && Array.isArray(this.settings.domainScopes) && this.settings.domainScopes.length) {
      this.batchUploader.setScope({
        rootDomains: normalizeRootDomains(this.settings.domainScopes),
        includeSubdomains: this.settings.includeSubdomains !== false
      });
    }
    // Re-apply the persisted project/config snapshot so a respawn before the first upload still
    // binds the session to its project (scope is re-applied above via its own flat keys).
    const pendingSessionConfig = (await chrome.storage.local.get('pendingSessionConfig')).pendingSessionConfig;
    if (pendingSessionConfig && typeof pendingSessionConfig === 'object') {
      this.batchUploader.setConfig(pendingSessionConfig);
    }
    this.isCapturing = this.settings.isCapturing || false;

    // --- durability wiring (S2): back the uploader with the persistent outbox and
    // let it clear the flush alarm once fully drained.
    this.batchUploader.setStore(this.outboxStore);
    this.batchUploader.setOnDrained(() => this.reconcileFlushAlarm(false));

    // NOTE: listeners are registered SYNCHRONOUSLY at module load (see bootstrap at the
    // bottom), not here — MV3 tears the worker down and routes the waking event only to
    // listeners present in the first turn. Their handlers gate on `this.ready`.

    // Restore the dedup set (so we don't re-fetch/re-upload files already captured in
    // this session) and resume any uploads a previous worker instance left unsent.
    await this.rehydrateDedup();
    // rehydrate() returns pending count, or -1 if the outbox READ failed. Treat both
    // "has files" and "unknown" as reasons to keep the flush alarm armed (fail safe).
    const pendingUploads = await this.batchUploader.rehydrate();
    this.reconcileFlushAlarm(pendingUploads !== 0);

    console.log('JSExtractor initialized', {
      sessionId: this.sessionId,
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
        // Defer handling until init has populated settings/session/stores. Hold the
        // sendResponse channel open ONLY for actions that actually respond — a fire-and-
        // forget message (e.g. dynamicScriptDetected) returning true would leak the port
        // until GC and log "message port closed before a response was received".
        this.ready.then(() => this.handleMessage(request, sender, sendResponse));
        return !FIRE_AND_FORGET_ACTIONS.has(request && request.action);
      }
    );

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

  // Rebuild the in-memory dedup set from the persistent store after a respawn.
  async rehydrateDedup() {
    try {
      const entries = (await this.dedupStore.getAll()) || [];
      for (const entry of entries) {
        if (entry && entry.contentHash) {
          this.capturedHashes.set(entry.contentHash, { url: entry.url, capturedAt: entry.capturedAt });
        }
      }
    } catch (e) {
      console.warn('Dedup rehydrate failed:', e);
    }
  }

  // Noise denylist + out-of-scope "exclude" mode → drop before capture.
  shouldSkipUrl(url, documentUrl) {
    // Never capture the recon workspace's own assets — the tool must not recon itself. Safety
    // net even when scope is wide-open (e.g. after "Open Workspace" loads localhost:3000).
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
    if (!this.isInScope(details.url)) return;
    if (this.isExtensionRequest(details)) return;
    if (this.shouldSkipUrl(details.url, details.documentUrl)) return;

    const authContext = this.authTracker.consume(details.requestId, details.url);
    const fileMetadata = this.extractMetadata(details, authContext);
    
    this.processingQueue.push({
      metadata: fileMetadata,
      tabId: details.tabId,
      frameId: details.frameId
    });

    this.scheduleQueueProcessing();
  }

  async processQueue() {
    if (this.processingQueue.length === 0) return;

    const batch = this.processingQueue.splice(0, 10);
    
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

    if (this.processingQueue.length > 0) {
      setTimeout(() => this.processQueue(), 100);
    }
  }

  async processFile(item) {
    const { metadata, tabId, frameId } = item;
    const url = metadata.url;

    console.log('Processing:', url);

    let contentResult = await this.contentFetcher.fetch(url, {});

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
    let sourceMapFetchStatus = this.settings.captureSourceMaps ? 'not_detected' : 'disabled';
    let sourceMapFetchError = null;
    if (this.settings.captureSourceMaps) {
      detectedSourceMapUrl = this.sourceMapDetector.detect(content, url);

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
    }

    const dependencies = this.settings.resolveDependencies
      ? this.dependencyExtractor.extract(content, url)
      : [];

    const contentByteLength = this.getContentByteLength(content);

    // Per-asset size cap (popup "Max asset size" slider). Skips the single file
    // without stopping capture — unlike the hard limits in enforceLimits().
    const maxAssetBytes = (this.settings.maxAssetMb || 8) * 1024 * 1024;
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
    if (this.capturedHashes.has(contentHash)) {
      const existingCapture = this.capturedHashes.get(contentHash);
      console.log(`Skipping duplicate content (hash: ${contentHash.substring(0, 8)}...) - same content as ${existingCapture.url}`);
      return;
    }
    
    // Check if this URL was captured with different content
    const existingFile = this.capturedFiles.get(url);
    if (existingFile && existingFile.contentHash !== contentHash) {
      console.log(`Re-capturing URL with changed content - old hash: ${existingFile.contentHash.substring(0, 8)}..., new hash: ${contentHash.substring(0, 8)}...`);
      // Remove old content hash tracking (memory + persistent store, so a superseded
      // version isn't resurrected on respawn and dedupStore can't grow unbounded).
      this.capturedHashes.delete(existingFile.contentHash);
      this.dedupStore.delete(existingFile.contentHash).catch(() => {});
    }

    const fileObject = {
      url: url,
      contentHash: contentHash,
      sessionId: this.sessionId,
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
      sourceMapFetchStatus: sourceMapFetchStatus,
      sourceMapFetchError: sourceMapFetchError,
      dependencies: dependencies,
      initiator: metadata.initiator,
      documentUrl: metadata.documentUrl,
      needsServerProcessing: sourceMapData !== null || dependencies.length > 0
    };

    // Track both URL and content hash for version-aware deduplication
    this.capturedFiles.set(url, fileObject);
    this.capturedHashes.set(contentHash, {url: url, capturedAt: fileObject.capturedAt});
    // Persist the dedup entry so a respawn won't re-fetch/re-hash/re-upload this file.
    try { await this.dedupStore.put(contentHash, { contentHash, url, capturedAt: fileObject.capturedAt }); }
    catch (e) { /* dedup is an optimization; a miss just re-uploads (server dedupes) */ }

    this.totalCapturedBytes += contentByteLength;
    this.processingStats.processedFiles += 1;

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
      if (!this.capturedFiles.has(depUrl)) {
        this.processingQueue.push({
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

    await this.batchUploader.enqueue(fileObject);
    // There is now durable pending work — ensure the cold-respawn flush alarm exists.
    this.reconcileFlushAlarm(true);

    this.notifyUI({
      action: 'fileProcessed',
      file: {
        url: url,
        size: contentByteLength,
        hasSourceMap: fileObject.hasSourceMap,
        dependencyCount: dependencies.length
      }
    });
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
    if (this.capturedFiles.size >= this.limits.maxFiles) {
      this.handleLimitExceeded('Maximum file count reached.');
      return false;
    }
    if (this.totalCapturedBytes + newBytes > this.limits.maxTotalBytes) {
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
    // No explicit scope defined for this session?
    if (!this.settings.useDomainScope ||
        this.settings.domainScopes.length === 0) {
      // Fail CLOSED: capture NOTHING unless the operator has explicitly opted into
      // wide-open capture. The old behaviour returned true here, which made capture
      // silently follow whatever tab you were on — grabbing out-of-scope engagement
      // data (e.g. docs.google.com) and even the workspace's own JS. "Capture every
      // tab" is now an explicit, loudly-badged choice, not a silent default.
      return this.settings.captureEverything === true;
    }

    try {
      const urlObj = new URL(url);
      const hostname = urlObj.hostname.toLowerCase();
      
      return this.settings.domainScopes.some(scope => {
        const trimmed = scope.trim().toLowerCase();
        if (!trimmed) return false;
        
        // Exact domain match
        if (hostname === trimmed) return true;

        // Subdomain match (gated by includeSubdomains; defaults true).
        if (this.settings.includeSubdomains !== false && hostname.endsWith('.' + trimmed)) return true;

        return false;
      });
    } catch (e) {
      return false;
    }
  }

  isExtensionRequest(details) {
    return details.initiator && 
           details.initiator.startsWith('chrome-extension://');
  }

  scheduleQueueProcessing() {
    if (this.processingTimer) {
      clearTimeout(this.processingTimer);
    }
    
    this.processingTimer = setTimeout(() => {
      this.processQueue();
    }, 500);
  }

  async loadSettings() {
    const result = await chrome.storage.local.get([
      'domainScopes',
      'useDomainScope',
      'captureEverything',
      'performAnalysisOnUpload',
      'captureSourceMaps',
      'resolveDependencies',
      'isCapturing',
      'captureAuthContext',
      'includeSubdomains',
      'workspaceUrl',
      'pairingToken',
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
      resolveDependencies: result.resolveDependencies !== false,
      isCapturing: result.isCapturing || false,
      captureAuthContext: result.captureAuthContext !== false,
      // --- redesigned popup settings ---
      // includeSubdomains MUST default true to preserve today's always-match
      // subdomain capture behaviour (isInScope) for existing users.
      includeSubdomains: result.includeSubdomains !== false,
      workspaceUrl: result.workspaceUrl || '',
      // Operator-pairing Bearer token. Empty => unauthenticated ingest (shared capture
      // tenant), i.e. today's behavior; a valid token routes captures to the operator's tenant.
      pairingToken: result.pairingToken || '',
      muteNoise: result.muteNoise !== false,
      outOfScopeMode: result.outOfScopeMode || 'tag',
      // Clamp to the 10 MB backend ceiling so a legacy stored value (from the old
      // 25 MB slider) can't wave through files the server will 422.
      maxAssetMb: Math.min(10, typeof result.maxAssetMb === 'number' ? result.maxAssetMb : 8),
      denyDefaultProfile: result.denyDefaultProfile !== false,
      denyRules: Array.isArray(result.denyRules) ? result.denyRules : DEFAULT_DENY_RULES
    };
  }

  notifyUI(message) {
    chrome.runtime.sendMessage(message).catch(() => {});
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

  async persistCaptureState(isCapturing) {
    try {
      await chrome.storage.local.set({ isCapturing });
    } catch (e) {
      console.warn('Failed to persist capture state', e);
    }
  }

  handleMessage(request, sender, sendResponse) {
    const handlers = {
      startCapture: () => this.startCapture(sendResponse),
      newSession: (req) => this.newSession(req, sendResponse),
      stopCapture: () => this.stopCapture(sendResponse),
      getFiles: () => this.getFiles(sendResponse),
      clearFiles: () => this.clearFiles(sendResponse),
      getStatus: () => this.getStatus(sendResponse),
      updateSettings: (req) => this.updateSettings(req, sendResponse),
      getExportData: () => this.getExportData(sendResponse),
      testConnection: () => this.workspaceClient.testConnection().then(sendResponse),
      analyzeSession: () => this.workspaceClient.analyzeSession().then(sendResponse),
      getAnalysisProgress: () => this.workspaceClient.getAnalysisProgress().then(sendResponse),
      listProjects: () => this.listProjects(sendResponse),
      createProject: (req) => this.workspaceClient.createProject(req.project).then(sendResponse),
      dynamicScriptDetected: (req) => this.handleDynamicScript(req, sender)
    };

    const handler = handlers[request.action];
    if (handler) {
      handler(request);
    }
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

    this.processingQueue.push({
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
    this.rescanActiveTab();
    sendResponse({ success: true, sessionId: this.sessionId });
  }

  async newSession(request, sendResponse) {
    // Drop not-yet-processed capture requests BEFORE rotating the id, so a straggler
    // can't be stamped with the new session id (fileObject.sessionId is set at process
    // time). Already-built batches in the uploader keep their own old per-file id, so
    // flush them under the previous session first.
    this.processingQueue = [];
    await this.batchUploader.flushAll();

    // Rotate to a fresh, PERSISTED session id and drop the previous session's captured
    // state (mirrors clearFiles) so the new session starts clean and survives respawns.
    this.sessionId = await this.sessionStore.rotate();
    this.capturedFiles.clear();
    this.capturedHashes.clear();
    // Reset the persistent dedup set for the new session. The outbox is intentionally
    // NOT cleared — any still-unsent files carry their own (old) per-file session id.
    this.dedupStore.clear().catch(() => {});
    this.authTracker.clear();
    this.totalCapturedBytes = 0;
    this.processingStats = {
      processedFiles: 0,
      failedFiles: 0,
      lastFailureReason: null,
      lastFailureUrl: null,
      lastFailureMessage: null
    };

    // Apply the client-resolved effective config. The popup resolved (project.defaults +
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

    // Reconstruct the resolved effective config (scope + non-scope groups) and map to storage.
    const effective = { scope: { rootDomains, includeSubdomains }, ...captureConfig };
    const patch = settingsFromConfig(effective);
    Object.assign(this.settings, patch);
    // Persist the flat gate keys AND the project snapshot together. The snapshot lets a worker
    // respawn before the first upload still bind the session to its project — scope persists
    // via the flat keys, but projectId/captureConfig/overrideKeys need their own key.
    await chrome.storage.local.set({ ...patch, pendingSessionConfig: { projectId, captureConfig, overrideKeys } });

    // Uploader: scope + project/config snapshot + analyze flag (mirrors updateSettings's sync).
    this.batchUploader.setScope({ rootDomains, includeSubdomains });
    this.batchUploader.setConfig({ projectId, captureConfig, overrideKeys });
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);

    // A fresh session on an already-loaded page should still capture that page (parity with
    // startCapture) — webRequest won't refire for JS that loaded before the rotation.
    if (this.isCapturing) this.rescanActiveTab();

    sendResponse({
      success: true,
      sessionId: this.sessionId,
      scope: { rootDomains, includeSubdomains },
      projectId,
      overrideKeys
    });
  }

  async stopCapture(sendResponse) {
    this.isCapturing = false;
    this.persistCaptureState(false);
    await this.batchUploader.flushAll();
    sendResponse({
      success: true,
      fileCount: this.capturedFiles.size,
      uploader: this.batchUploader.getStats()
    });
  }

  getFiles(sendResponse) {
    const files = Array.from(this.capturedFiles.values()).map(f => ({
      url: f.url,
      size: f.contentLength,
      hasSourceMap: f.hasSourceMap,
      dependencyCount: f.dependencies.length,
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
      sessionId: this.sessionId,
      isCapturing: this.isCapturing
    });
  }

  clearFiles(sendResponse) {
    this.capturedFiles.clear();
    this.capturedHashes.clear();
    this.dedupStore.clear().catch(() => {});
    this.processingQueue = [];
    this.authTracker.clear();
    this.totalCapturedBytes = 0;
    this.processingStats = {
      processedFiles: 0,
      failedFiles: 0,
      lastFailureReason: null,
      lastFailureUrl: null,
      lastFailureMessage: null
    };
    sendResponse({ success: true });
  }

  getStatus(sendResponse) {
    let mapsCount = 0;
    let secretCount = 0;
    for (const f of this.capturedFiles.values()) {
      if (f.hasSourceMap) mapsCount += 1;
      secretCount += f.secretCount || 0;
    }
    sendResponse({
      isCapturing: this.isCapturing,
      sessionId: this.sessionId,
      fileCount: this.capturedFiles.size,
      mapsCount,
      secretCount,
      queueLength: this.processingQueue.length,
      processingStats: this.processingStats,
      uploader: this.batchUploader.getStats(),
      settings: this.settings
    });
  }

  async updateSettings(request, sendResponse) {
    this.settings = { ...this.settings, ...request.settings };
    if (typeof this.settings.captureAuthContext !== 'boolean') {
      this.settings.captureAuthContext = true;
    }
    await chrome.storage.local.set(this.settings);
    this.batchUploader.setEndpoint(this.workspaceClient.resolveApiBase());
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    // Push a changed pairing token to the uploader (workspace-client reads it live via
    // getSettings, so it needs no push). A cleared token reverts to shared-tenant ingest.
    this.batchUploader.setAuthToken(this.settings.pairingToken);
    sendResponse({ success: true });
  }

  async listProjects(sendResponse) {
    // Live list refreshes the cache; a workspace blip falls back to the cached list so the
    // popup's engagement picker still renders. Never throws.
    const { projects, source } = await listProjectsWithCache(
      () => this.workspaceClient.listProjects(),
      chrome.storage.local
    );
    sendResponse({ success: true, projects, source });
  }

  getExportData(sendResponse) {
    const files = Array.from(this.capturedFiles.values());

    try {
      const exportData = buildExportData({
        sessionId: this.sessionId,
        files,
        includeContent: false,
        version: '3.0.0'
      });

      sendResponse({
        success: true,
        filename: `js-extraction-${this.sessionId}.json`,
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
