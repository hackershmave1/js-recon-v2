// auth-context.js — captures the short-lived request auth context (Authorization,
// Cookie, CSRF/token headers) for in-scope script requests so a reconstructed request
// can carry the same credentials. Extracted from background.js so the service worker
// stays focused on orchestration.
//
// Lifecycle: record() on onBeforeSendHeaders (keyed by requestId), consume() when the
// matching onCompleted fires (single-use), discard() on onErrorOccurred. Entries are
// TTL-bounded and capped so a long browse can't grow the map without limit.

// Only these request headers are ever captured; everything else is ignored so we never
// persist arbitrary request headers.
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

export class AuthContextTracker {
  // Dependencies are injected so the tracker doesn't reach back into JSExtractor:
  //   getSettings()        -> the live settings object (for captureAuthContext)
  //   isInScope(url)       -> whether the URL is in the capture scope
  //   isExtensionRequest(d)-> whether the request originated from an extension
  constructor({ getSettings, isInScope, isExtensionRequest } = {}) {
    this.getSettings = typeof getSettings === 'function' ? getSettings : () => ({});
    this.isInScope = typeof isInScope === 'function' ? isInScope : () => true;
    this.isExtensionRequest = typeof isExtensionRequest === 'function' ? isExtensionRequest : () => false;
    this.requestAuthContexts = new Map();
    this.authContextTtlMs = 5 * 60 * 1000;
    this.maxAuthContextEntries = 4000;
  }

  // onBeforeSendHeaders: stash the allowlisted auth headers for this requestId. The
  // caller gates on capture being active; this method enforces scope + settings.
  record(details) {
    if (!details || !details.requestId || !details.url) return;
    if (this.isExtensionRequest(details)) return;
    if (!this.isInScope(details.url)) return;
    const settings = this.getSettings() || {};
    if (settings.captureAuthContext === false) return;
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

  // onCompleted: single-use retrieval of the context for a requestId, dropped after read
  // and rejected if it expired or no longer matches the (possibly redirected) URL.
  consume(requestId, requestUrl) {
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

  // onErrorOccurred: drop a stashed context whose request never completed.
  discard(requestId) {
    if (!requestId) return;
    this.requestAuthContexts.delete(requestId);
  }

  // Drop every stashed context (New Session / Clear captures).
  clear() {
    this.requestAuthContexts.clear();
  }

  // Domain filtering was removed; auth context is captured for every in-scope URL
  // (scope is already enforced by the injected isInScope gate in record()).
  shouldCaptureAuthContextForUrl(url) {
    return true;
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
}
