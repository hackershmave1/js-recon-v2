// background.js - Main service worker
import { ContentFetcher } from './modules/content-fetcher.js';
import { DependencyExtractor } from './modules/dependency-extractor.js';
import { SourceMapDetector } from './modules/sourcemap-detector.js';
import { Decompressor } from './modules/decompressor.js';
import { BatchUploader } from './modules/batch-uploader.js';
import { SessionStore } from './modules/session-store.js';
import { IdbStore } from './modules/idb-store.js';
import { RepPlusBridge } from './modules/rep-plus-bridge.js';
import { buildExportData } from './modules/export-builder.js';
import { classifyAsset, isThirdParty, matchesDenylist, countSecrets } from './modules/asset-classifier.js';

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

const AUTH_HEADER_ALLOWLIST = new Set([
  'authorization',
  'cookie',
  'x-api-key',
  'x-auth-token',
  'x-csrf-token',
  'x-xsrf-token',
  'x-access-token',
  'x-session-token'
]);

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
    this.requestAuthContexts = new Map();
    this.authContextTtlMs = 5 * 60 * 1000;
    this.maxAuthContextEntries = 4000;

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
    this.repPlusBridge = new RepPlusBridge();
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
    if (this.settings.useLocalApi !== true) {
      this.settings.useLocalApi = true;
      await chrome.storage.local.set({ useLocalApi: true });
    }
    if (this.settings.apiEndpoint) {
      this.batchUploader.setEndpoint(this.settings.apiEndpoint);
    }
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    this.batchUploader.setAnalysisOptions(this.settings.analysisOptions || {});
    // Re-apply a persisted scope so uploads keep tagging the session even if the
    // service worker recycled after a new session was started.
    if (this.settings.useDomainScope && Array.isArray(this.settings.domainScopes) && this.settings.domainScopes.length) {
      this.batchUploader.setScope({
        rootDomains: normalizeRootDomains(this.settings.domainScopes),
        includeSubdomains: this.settings.includeSubdomains !== false
      });
    }
    this.repPlusBridge.setExtensionId(this.settings.repPlusExtensionId || '');
    this.isCapturing = this.settings.isCapturing || false;
    if (this.settings.autoStart) {
      this.isCapturing = true;
      await this.persistCaptureState(true);
    }
    
    // Initialize rep+ bridge for optional integration
    await this.repPlusBridge.initialize();

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
      repPlusAvailable: this.repPlusBridge.isRepPlusAvailable,
      pendingUploads
    });
  }

  // Registered synchronously at module load (bootstrap below). Because a cold-woken
  // worker may not have finished initialize() yet, each handler defers its state-using
  // work behind `this.ready` (the initialize() promise). The webRequest detail objects
  // are plain data, so they stay valid inside the deferred continuation.
  setupListeners() {
    chrome.webRequest.onBeforeSendHeaders.addListener(
      (details) => { this.ready.then(() => this.captureRequestAuthContext(details)); },
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
      (details) => { this.ready.then(() => this.discardRequestAuthContext(details.requestId)); },
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
    if (matchesDenylist(url, this.settings.denyRules || [], this.settings.denyDefaultProfile !== false)) {
      return true;
    }
    if (this.settings.outOfScopeMode === 'exclude' && documentUrl && isThirdParty(url, documentUrl)) {
      return true;
    }
    return false;
  }

  async handleRequest(details) {
    if (!this.isCapturing) return;
    if (!this.isInScope(details.url)) return;
    if (this.isExtensionRequest(details)) return;
    if (this.shouldSkipUrl(details.url, details.documentUrl)) return;

    const authContext = this.consumeRequestAuthContext(details.requestId, details.url);
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
      detectedSourceMapUrl = this.sourceMapDetector.detect(content, url, {
        allowFallback: this.settings.allowSourceMapFallback
      });

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

    let dependencies = this.settings.resolveDependencies
      ? this.dependencyExtractor.extract(content, url)
      : [];

    let repPlusSummary = this.repPlusBridge.summarize(null);
    repPlusSummary.importedHintCount = 0;
    if (this.settings.importRepPlusSignals && this.repPlusBridge.isRepPlusAvailable) {
      const repPlusResults = await this.repPlusBridge.getRepPlusResults(tabId);
      repPlusSummary = this.repPlusBridge.summarize(repPlusResults);
      if (this.settings.resolveDependencies) {
        const repPlusHints = this.repPlusBridge.extractScriptImportHints(repPlusResults, url);
        dependencies = this.mergeDependencies(dependencies, repPlusHints);
        repPlusSummary.importedHintCount = repPlusHints.length;
      }
    }

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
      repPlusSummary: repPlusSummary,
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

  captureRequestAuthContext(details) {
    if (!this.isCapturing) return;
    if (!details || !details.requestId || !details.url) return;
    if (this.isExtensionRequest(details)) return;
    if (!this.isInScope(details.url)) return;
    if (this.settings?.captureAuthContext === false) return;
    if (!this.shouldCaptureAuthContextForUrl(details.url)) return;

    const capturedHeaders = {};
    for (const header of details.requestHeaders || []) {
      const rawName = typeof header?.name === 'string' ? header.name : '';
      const rawValue = typeof header?.value === 'string' ? header.value : '';
      const name = rawName.trim().toLowerCase();
      const value = this.sanitizeAuthHeaderValue(rawValue);
      if (!name || !AUTH_HEADER_ALLOWLIST.has(name) || !value) {
        continue;
      }
      capturedHeaders[name] = value;
    }

    if (Object.keys(capturedHeaders).length === 0) {
      return;
    }

    const domain = this.getHostname(details.url);
    const cookieNames = this.extractCookieNames(capturedHeaders.cookie || '');
    const authContext = {
      schemaVersion: '1.0',
      source: 'extension.webRequest',
      capturedAt: new Date().toISOString(),
      domain: domain,
      requestUrl: details.url,
      headers: capturedHeaders,
      cookie: {
        present: cookieNames.length > 0,
        names: cookieNames,
        count: cookieNames.length
      }
    };

    this.requestAuthContexts.set(details.requestId, {
      capturedAt: Date.now(),
      context: authContext
    });
    this.pruneRequestAuthContexts();
  }

  consumeRequestAuthContext(requestId, requestUrl) {
    if (!requestId) {
      return null;
    }
    const entry = this.requestAuthContexts.get(requestId);
    if (!entry) {
      return null;
    }
    this.requestAuthContexts.delete(requestId);
    if ((Date.now() - entry.capturedAt) > this.authContextTtlMs) {
      return null;
    }
    if (!this.isAuthContextValidForUrl(entry.context, requestUrl)) {
      return null;
    }
    return entry.context;
  }

  discardRequestAuthContext(requestId) {
    if (!requestId) return;
    this.requestAuthContexts.delete(requestId);
  }

  shouldCaptureAuthContextForUrl(url) {
    const configuredDomains = Array.isArray(this.settings?.authContextDomains)
      ? this.settings.authContextDomains
      : [];
    if (configuredDomains.length === 0) {
      return true;
    }
    const hostname = this.getHostname(url);
    if (!hostname) {
      return false;
    }
    return configuredDomains.some((scope) => this.hostnameMatchesScope(hostname, scope));
  }

  isAuthContextValidForUrl(authContext, url) {
    if (!authContext || typeof authContext !== 'object') {
      return false;
    }
    const contextDomain = this.getHostname(authContext.domain || authContext.requestUrl || '');
    const urlDomain = this.getHostname(url);
    if (!contextDomain || !urlDomain) {
      return false;
    }
    return this.hostnameMatchesScope(urlDomain, contextDomain);
  }

  pruneRequestAuthContexts() {
    const now = Date.now();
    for (const [requestId, entry] of this.requestAuthContexts.entries()) {
      if ((now - entry.capturedAt) > this.authContextTtlMs) {
        this.requestAuthContexts.delete(requestId);
      }
    }
    if (this.requestAuthContexts.size <= this.maxAuthContextEntries) {
      return;
    }
    const excess = this.requestAuthContexts.size - this.maxAuthContextEntries;
    const keys = Array.from(this.requestAuthContexts.keys()).slice(0, excess);
    for (const requestId of keys) {
      this.requestAuthContexts.delete(requestId);
    }
  }

  sanitizeAuthHeaderValue(value) {
    if (typeof value !== 'string') return '';
    const cleaned = value.replace(/\r/g, ' ').replace(/\n/g, ' ').trim();
    if (!cleaned) return '';
    const maxLength = 8192;
    return cleaned.length > maxLength ? cleaned.slice(0, maxLength) : cleaned;
  }

  extractCookieNames(cookieHeader) {
    if (typeof cookieHeader !== 'string' || !cookieHeader.trim()) {
      return [];
    }
    const names = [];
    const seen = new Set();
    for (const segment of cookieHeader.split(';')) {
      const name = segment.split('=')[0].trim();
      if (!name) continue;
      const key = name.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      names.push(name);
      if (names.length >= 64) break;
    }
    return names;
  }

  getHostname(value) {
    if (!value || typeof value !== 'string') {
      return null;
    }
    try {
      const url = value.startsWith('http://') || value.startsWith('https://')
        ? new URL(value)
        : new URL(`https://${value}`);
      return (url.hostname || '').toLowerCase();
    } catch (e) {
      return null;
    }
  }

  hostnameMatchesScope(hostname, scope) {
    const target = (hostname || '').trim().toLowerCase();
    const normalizedScope = (scope || '').trim().toLowerCase().replace(/^\./, '');
    if (!target || !normalizedScope) {
      return false;
    }
    if (target === normalizedScope) {
      return true;
    }
    return target.endsWith(`.${normalizedScope}`);
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

  mergeDependencies(existing, additional) {
    const merged = [];
    const seen = new Set();
    const all = [...(existing || []), ...(additional || [])];

    for (const dep of all) {
      if (!dep) continue;
      const resolved = dep.resolvedUrl || dep.url;
      const key = `${dep.type || 'unknown'}|${resolved || ''}`;
      if (!resolved || seen.has(key)) continue;
      seen.add(key);
      merged.push({
        ...dep,
        resolvedUrl: resolved
      });
    }

    return merged;
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
    if (!this.settings.useDomainScope || 
        this.settings.domainScopes.length === 0) {
      return true;
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
      'apiEndpoint',
      'useLocalApi',
      'autoStart',
      'performAnalysisOnUpload',
      'captureSourceMaps',
      'resolveDependencies',
      'importRepPlusSignals',
      'repPlusExtensionId',
      'allowSourceMapFallback',
      'isCapturing',
      'exportIncludeContent',
      'captureAuthContext',
      'authContextDomains',
      'includeSubdomains',
      'workspaceUrl',
      'apiKey',
      'muteNoise',
      'outOfScopeMode',
      'maxAssetMb',
      'denyDefaultProfile',
      'denyRules',
      'scanProfile',
      'analysisOptions'
    ]);

    return {
      domainScopes: result.domainScopes || [],
      useDomainScope: result.useDomainScope || false,
      apiEndpoint: result.apiEndpoint || 'http://localhost:3000/api/save-files',
      useLocalApi: result.useLocalApi !== false,
      autoStart: result.autoStart || false,
      performAnalysisOnUpload: result.performAnalysisOnUpload === true,
      captureSourceMaps: result.captureSourceMaps !== false,
      resolveDependencies: result.resolveDependencies !== false,
      importRepPlusSignals: result.importRepPlusSignals === true,
      repPlusExtensionId: result.repPlusExtensionId || '',
      allowSourceMapFallback: result.allowSourceMapFallback || false,
      isCapturing: result.isCapturing || false,
      exportIncludeContent: result.exportIncludeContent === true,
      captureAuthContext: result.captureAuthContext !== false,
      authContextDomains: Array.isArray(result.authContextDomains) ? result.authContextDomains : [],
      // --- redesigned popup settings ---
      // includeSubdomains MUST default true to preserve today's always-match
      // subdomain capture behaviour (isInScope) for existing users.
      includeSubdomains: result.includeSubdomains !== false,
      workspaceUrl: result.workspaceUrl || '',
      apiKey: result.apiKey || '',
      muteNoise: result.muteNoise !== false,
      outOfScopeMode: result.outOfScopeMode || 'tag',
      // Clamp to the 10 MB backend ceiling so a legacy stored value (from the old
      // 25 MB slider) can't wave through files the server will 422.
      maxAssetMb: Math.min(10, typeof result.maxAssetMb === 'number' ? result.maxAssetMb : 8),
      denyDefaultProfile: result.denyDefaultProfile !== false,
      denyRules: Array.isArray(result.denyRules) ? result.denyRules : DEFAULT_DENY_RULES,
      // Scan type: preset name + extractor toggles forwarded as analysisOptions on
      // upload. Empty options → backend defaults; profile drives the popup selection.
      scanProfile: result.scanProfile || 'standard',
      analysisOptions: (result.analysisOptions && typeof result.analysisOptions === 'object') ? result.analysisOptions : {}
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
      exportFiles: () => this.exportFiles(sendResponse),
      testConnection: () => this.testConnection(sendResponse),
      analyzeSession: () => this.analyzeSession(sendResponse),
      getAnalysisProgress: () => this.getAnalysisProgress(sendResponse),
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

  startCapture(sendResponse) {
    this.isCapturing = true;
    this.persistCaptureState(true);
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
    this.requestAuthContexts.clear();
    this.totalCapturedBytes = 0;
    this.processingStats = {
      processedFiles: 0,
      failedFiles: 0,
      lastFailureReason: null,
      lastFailureUrl: null,
      lastFailureMessage: null
    };

    // Apply the chosen scope: root domains both gate capture (reusing domainScopes)
    // and seed the app-side session scope via the save-files metadata.
    const scope = (request && request.scope) || {};
    const rootDomains = normalizeRootDomains(scope.rootDomains);
    const includeSubdomains = scope.includeSubdomains !== false;
    // A blank scope must RESET capture gating (no else → the new session would silently
    // inherit the previous session's domainScopes and capture out of its intended scope).
    this.settings.domainScopes = rootDomains;
    this.settings.useDomainScope = rootDomains.length > 0;
    this.settings.includeSubdomains = includeSubdomains;
    await chrome.storage.local.set({
      domainScopes: this.settings.domainScopes,
      useDomainScope: this.settings.useDomainScope,
      includeSubdomains
    });
    this.batchUploader.setScope({ rootDomains, includeSubdomains });

    sendResponse({ success: true, sessionId: this.sessionId, scope: { rootDomains, includeSubdomains } });
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
      repPlusImportedHints: f.repPlusSummary?.importedHintCount || 0,
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
    this.requestAuthContexts.clear();
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

  async testConnection(sendResponse) {
    const target = this.resolveApiBase() + '/api/health';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    const startedAt = Date.now();
    try {
      const headers = {};
      if (this.settings?.apiKey) headers['x-api-key'] = this.settings.apiKey;
      const resp = await fetch(target, { method: 'GET', headers, signal: controller.signal });
      clearTimeout(timer);
      sendResponse({ ok: resp.ok, status: resp.status, latencyMs: Date.now() - startedAt });
    } catch (error) {
      clearTimeout(timer);
      sendResponse({ ok: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') });
    }
  }

  deriveOrigin(url) {
    try { return new URL(url).origin; } catch (e) { return ''; }
  }

  // Workspace API origin (no trailing slash) for non-ingestion calls (health, analyze).
  resolveApiBase() {
    const s = this.settings || {};
    let base = (s.workspaceUrl || '').trim()
      || this.deriveOrigin(s.apiEndpoint)
      || 'http://localhost:3000';
    // A scheme-less workspace URL (e.g. "localhost:3000") would resolve relative to the
    // extension origin and fail — prepend http:// so it's an absolute URL.
    if (base && !/^[a-z][a-z0-9+.-]*:\/\//i.test(base)) base = 'http://' + base;
    return base.replace(/\/+$/, '');
  }

  // Trigger on-demand analysis of the current session (the decoupled path): flush any
  // unsent captures first, then kick the backend's async threaded job so the single
  // worker isn't blocked inline during capture.
  async analyzeSession(sendResponse) {
    // Best-effort flush so the server has as many captures as possible before analyzing,
    // but BOUNDED — a slow/unreachable workspace must not stall the analyze trigger (the
    // outbox timer/alarm keeps draining in the background regardless).
    try {
      await Promise.race([
        this.batchUploader.flushAll(),
        new Promise((resolve) => setTimeout(resolve, 8000))
      ]);
    } catch (e) {
      // A flush failure shouldn't block analyzing what did upload.
      console.warn('Flush before analyze failed:', e);
    }
    const target = `${this.resolveApiBase()}/api/sessions/${encodeURIComponent(this.sessionId)}/analyze/start`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (this.settings?.apiKey) headers['x-api-key'] = this.settings.apiKey;
      const resp = await fetch(target, {
        method: 'POST',
        headers,
        body: JSON.stringify({ options: this.settings.analysisOptions || {} }),
        signal: controller.signal
      });
      clearTimeout(timer);
      if (!resp.ok) {
        sendResponse({ success: false, status: resp.status, error: `HTTP ${resp.status}` });
        return;
      }
      const data = await resp.json();
      sendResponse({ success: true, started: data.started !== false, message: data.message, job: data.job });
    } catch (error) {
      clearTimeout(timer);
      sendResponse({ success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') });
    }
  }

  // Poll analysis progress for the current session (drives the popup's per-file feed).
  async getAnalysisProgress(sendResponse) {
    const target = `${this.resolveApiBase()}/api/sessions/${encodeURIComponent(this.sessionId)}/analyze/progress`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const headers = {};
      if (this.settings?.apiKey) headers['x-api-key'] = this.settings.apiKey;
      const resp = await fetch(target, { method: 'GET', headers, signal: controller.signal });
      clearTimeout(timer);
      if (!resp.ok) {
        sendResponse({ success: false, status: resp.status });
        return;
      }
      const data = await resp.json();
      sendResponse({ success: true, job: data.job || null });
    } catch (error) {
      clearTimeout(timer);
      sendResponse({ success: false, error: error?.name === 'AbortError' ? 'timeout' : (error?.message || 'unreachable') });
    }
  }

  async updateSettings(request, sendResponse) {
    this.settings = { ...this.settings, ...request.settings };
    this.settings.useLocalApi = true;
    if (!Array.isArray(this.settings.authContextDomains)) {
      this.settings.authContextDomains = [];
    }
    if (typeof this.settings.captureAuthContext !== 'boolean') {
      this.settings.captureAuthContext = true;
    }
    await chrome.storage.local.set(this.settings);
    if (this.settings.apiEndpoint) {
      this.batchUploader.setEndpoint(this.settings.apiEndpoint);
    }
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    this.batchUploader.setAnalysisOptions(this.settings.analysisOptions || {});
    if (Object.prototype.hasOwnProperty.call(request.settings || {}, 'repPlusExtensionId')) {
      this.repPlusBridge.setExtensionId(this.settings.repPlusExtensionId || '');
      await this.repPlusBridge.initialize();
    }
    sendResponse({ success: true });
  }

  getExportData(sendResponse) {
    const files = Array.from(this.capturedFiles.values());
    const includeContent = this.settings?.exportIncludeContent === true;

    try {
      const exportData = buildExportData({
        sessionId: this.sessionId,
        files,
        includeContent,
        version: '3.0.0'
      });
      const estimatedBytes = JSON.stringify(exportData).length;
      const maxMessageBytes = 24 * 1024 * 1024;

      if (includeContent && estimatedBytes > maxMessageBytes) {
        const estimatedMb = (estimatedBytes / (1024 * 1024)).toFixed(2);
        sendResponse({
          success: false,
          error: `Export payload is too large (${estimatedMb} MB) for extension transfer. Disable "Include file contents in export" for metadata-only export.`
        });
        return;
      }

      sendResponse({
        success: true,
        filename: `js-extraction-${this.sessionId}.json`,
        includeContent,
        estimatedBytes,
        exportData
      });
    } catch (error) {
      console.error('Export payload build failed:', error);
      sendResponse({ success: false, error: error.message });
    }
  }

  exportFiles(sendResponse) {
    // Backward-compatible action alias.
    this.getExportData(sendResponse);
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
