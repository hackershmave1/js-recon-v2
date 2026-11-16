export class ContentFetcher {
  constructor() {
    this.cache = new Map();
    this.maxRetries = 3;
    // Per-attempt fetch timeout. Chrome imposes no default fetch timeout, and the capture
    // processing queue is strictly serial (background.js processQueue awaits processFile), so a
    // single blackholed in-scope asset (or its .map) would otherwise hang fetch() forever and
    // stall ALL capture. Abort each attempt so the queue advances. Overridable (tests set it
    // small). Mirrors the uploader's AbortController (modules/batch-uploader.js).
    this.fetchTimeoutMs = 30000;
  }

  // Single-shot GET, NO retry — for SPECULATIVE probes (the conventional `<file>.js.map`,
  // DEBT D45c) where a miss is the common case. ContentFetcher.fetch retries 3x with
  // 1s+2s backoff; on the strictly-serial capture queue that would add ~3 requests + ~3s
  // of latency for EVERY map-less in-scope file (review Finding A). One attempt, one abort
  // timeout, never throws. Not cached (a probe result shouldn't shadow a real later fetch).
  async fetchOnce(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.fetchTimeoutMs);
    try {
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        signal: controller.signal
      });
      if (!response.ok) {
        return { success: false, error: `HTTP ${response.status}` };
      }
      return { success: true, content: await response.text() };
    } catch (error) {
      const msg = error && error.name === 'AbortError'
        ? `timeout after ${this.fetchTimeoutMs}ms`
        : (error && error.message ? error.message : 'fetch failed');
      return { success: false, error: msg };
    } finally {
      clearTimeout(timer);
    }
  }

  // A script from a host the researcher has not decided on (ADR-0010 quarantine): fetched WITHOUT
  // the browser's cookies or HTTP auth for that site (`credentials: 'omit'`), so an unknown host can
  // never be sent the researcher's session, and capped in size. Single attempt, never throws: a
  // miss just means nothing is held. The caller follows no dependency or source map from it.
  async fetchQuarantined(url, { maxBytes = 10 * 1024 * 1024 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.fetchTimeoutMs);
    try {
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'omit',
        signal: controller.signal
      });
      if (!response.ok) return { success: false, error: `HTTP ${response.status}` };
      const declared = Number(response.headers.get('content-length'));
      if (declared > maxBytes) return { success: false, error: 'too_large' };
      const content = await response.text();
      if (content.length > maxBytes) return { success: false, error: 'too_large' };
      return { success: true, content };
    } catch (error) {
      return { success: false, error: error && error.name === 'AbortError' ? 'timeout' : 'fetch failed' };
    } finally {
      clearTimeout(timer);
    }
  }

  async fetch(url, options = {}) {
    if (this.cache.has(url)) {
      return { success: true, content: this.cache.get(url), cached: true };
    }

    for (let attempt = 0; attempt < this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.fetchTimeoutMs);
      try {
        const response = await fetch(url, {
          method: 'GET',
          credentials: 'include',
          headers: options.headers || {},
          signal: controller.signal
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const contentEncoding = response.headers.get('content-encoding') || 'identity';
        const isCompressedFile =
          url.endsWith('.gz') ||
          url.endsWith('.br') ||
          url.endsWith('.deflate');

        if (isCompressedFile) {
          const buffer = await response.arrayBuffer();
          return {
            success: true,
            content: buffer,
            isBinary: true,
            contentEncoding: contentEncoding
          };
        }

        const content = await response.text();
        this.cache.set(url, content);
        return {
          success: true,
          content: content,
          contentEncoding: contentEncoding
        };
      } catch (error) {
        // A timeout (AbortError) or network error is a failed attempt: back off and retry, then
        // give up with a failure result so the serial queue advances instead of hanging here.
        if (attempt === this.maxRetries - 1) {
          const msg = error && error.name === 'AbortError'
            ? `timeout after ${this.fetchTimeoutMs}ms`
            : (error && error.message ? error.message : 'fetch failed');
          return { success: false, error: msg };
        }
        await new Promise(resolve => setTimeout(resolve, Math.pow(2, attempt) * 1000));
      } finally {
        clearTimeout(timer);
      }
    }
  }
}
