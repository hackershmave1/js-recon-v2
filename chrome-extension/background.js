// background.js - Main service worker
import { ContentFetcher } from './modules/content-fetcher.js';
import { DependencyExtractor } from './modules/dependency-extractor.js';
import { SourceMapDetector } from './modules/sourcemap-detector.js';
import { Decompressor } from './modules/decompressor.js';
import { BatchUploader } from './modules/batch-uploader.js';
import { RepPlusBridge } from './modules/rep-plus-bridge.js';
import { buildExportData } from './modules/export-builder.js';

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
    this.sessionId = this.generateSessionId();
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
      maxFileBytes: 50 * 1024 * 1024,
      maxTotalBytes: 200 * 1024 * 1024,
      maxFiles: 2000
    };
    
    this.contentFetcher = new ContentFetcher();
    this.dependencyExtractor = new DependencyExtractor();
    this.sourceMapDetector = new SourceMapDetector();
    this.decompressor = new Decompressor();
    this.batchUploader = new BatchUploader();
    this.repPlusBridge = new RepPlusBridge();
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
    if (this.settings.useLocalApi !== true) {
      this.settings.useLocalApi = true;
      await chrome.storage.local.set({ useLocalApi: true });
    }
    if (this.settings.apiEndpoint) {
      this.batchUploader.setEndpoint(this.settings.apiEndpoint);
    }
    this.batchUploader.setPerformAnalysisOnUpload(this.settings.performAnalysisOnUpload === true);
    this.repPlusBridge.setExtensionId(this.settings.repPlusExtensionId || '');
    this.isCapturing = this.settings.isCapturing || false;
    if (this.settings.autoStart) {
      this.isCapturing = true;
      await this.persistCaptureState(true);
    }
    
    // Initialize rep+ bridge for optional integration
    await this.repPlusBridge.initialize();
    
    this.setupListeners();
    console.log('JSExtractor initialized', { sessionId: this.sessionId, repPlusAvailable: this.repPlusBridge.isRepPlusAvailable });
  }

  setupListeners() {
    chrome.webRequest.onBeforeSendHeaders.addListener(
      (details) => this.captureRequestAuthContext(details),
      {
        urls: ["<all_urls>"],
        types: ["script"]
      },
      ["requestHeaders", "extraHeaders"]
    );

    chrome.webRequest.onCompleted.addListener(
      (details) => this.handleRequest(details),
      { 
        urls: ["<all_urls>"],
        types: ["script"]
      },
      ["responseHeaders"]
    );

    chrome.webRequest.onErrorOccurred.addListener(
      (details) => this.discardRequestAuthContext(details.requestId),
      {
        urls: ["<all_urls>"],
        types: ["script"]
      }
    );

    chrome.runtime.onMessage.addListener(
      (request, sender, sendResponse) => {
        this.handleMessage(request, sender, sendResponse);
        return true;
      }
    );
  }

  async handleRequest(details) {
    if (!this.isCapturing) return;
    if (!this.isInScope(details.url)) return;
    if (this.isExtensionRequest(details)) return;

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
    if (!this.enforceLimits(contentByteLength)) {
      return;
    }
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
      // Remove old content hash tracking
      this.capturedHashes.delete(existingFile.contentHash);
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
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'Capture Stopped',
      message: message
    });
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
        
        // Subdomain match (must end with the scope domain)
        if (hostname.endsWith('.' + trimmed)) return true;
        
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

  generateSessionId() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
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
      'authContextDomains'
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
      authContextDomains: Array.isArray(result.authContextDomains) ? result.authContextDomains : []
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
      stopCapture: () => this.stopCapture(sendResponse),
      getFiles: () => this.getFiles(sendResponse),
      clearFiles: () => this.clearFiles(sendResponse),
      getStatus: () => this.getStatus(sendResponse),
      updateSettings: (req) => this.updateSettings(req, sendResponse),
      getExportData: () => this.getExportData(sendResponse),
      exportFiles: () => this.exportFiles(sendResponse),
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
    sendResponse({
      isCapturing: this.isCapturing,
      sessionId: this.sessionId,
      fileCount: this.capturedFiles.size,
      queueLength: this.processingQueue.length,
      processingStats: this.processingStats,
      uploader: this.batchUploader.getStats(),
      settings: this.settings
    });
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
extractor.initialize();
