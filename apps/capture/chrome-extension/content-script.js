(function() {
  'use strict';

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'fetchUrl') {
      fetchFromPage(request.url)
        .then(result => sendResponse(result))
        .catch(error => sendResponse({ success: false, error: error.message }));

      return true;
    }
    // Capture turned on (or a new session opened) on an ALREADY-loaded tab: the background
    // asks us to enumerate the JS the page loaded before capture was active. webRequest only
    // sees NEW script requests and nothing else re-reads a loaded page, so without this an
    // already-open tab captures nothing until the operator reloads.
    if (request.action === 'rescanScripts') {
      scanLoadedScripts();
      sendResponse({ ok: true });
      return false;
    }
  });

  async function fetchFromPage(url) {
    try {
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'include'
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const content = await response.text();

      return {
        success: true,
        content: content,
        contentType: response.headers.get('content-type')
      };
    } catch (error) {
      return {
        success: false,
        error: error.message
      };
    }
  }

  // Fire-and-forget report of one script URL to the background (same channel the
  // MutationObserver uses). Swallows the "worker respawning / context invalidated" rejection
  // so a transient service-worker gap never throws into page code.
  function emitScript(url, initiator) {
    try {
      const sent = chrome.runtime.sendMessage({
        action: 'dynamicScriptDetected',
        url,
        initiator,
        timestamp: new Date().toISOString()
      });
      if (sent && typeof sent.catch === 'function') sent.catch(() => {});
    } catch (e) {
      // Extension context torn down mid-navigation — the next load/rescan trigger retries.
    }
  }

  // Fire-and-forget report of one INLINE <script> body (DEBT D45a). Same channel/contract as
  // emitScript; the background applies scope + a content-based relevance filter and dedups by
  // content hash. Swallows the "worker respawning" rejection.
  function emitInline(pageUrl, content, ordinal) {
    try {
      const sent = chrome.runtime.sendMessage({
        action: 'inlineScriptDetected',
        pageUrl,
        content,
        ordinal,
        timestamp: new Date().toISOString()
      });
      if (sent && typeof sent.catch === 'function') sent.catch(() => {});
    } catch (e) {
      // Extension context torn down mid-navigation — the next load/rescan retries.
    }
  }

  // Running ordinal so the background can key an inline script's synthetic URL on POSITION
  // rather than content hash — a re-rendered slot then supersedes its prior version instead of
  // accumulating (DEBT D45a). Initial-scan scripts take stable document-order ordinals; nodes
  // added later continue past the last scan's count.
  let inlineSeq = 0;

  // An inline <script> worth reading: no src, and a classic/module JS type — never a JSON /
  // importmap / ld+json data island (no code to analyze).
  function isCapturableInlineScript(node) {
    if (!node || node.tagName !== 'SCRIPT' || node.src) return false;
    const type = (node.getAttribute('type') || '').trim().toLowerCase();
    return type === '' || type === 'text/javascript' || type === 'application/javascript' || type === 'module';
  }

  // Read every inline <script> body in document order and report each with a stable ordinal.
  function scanInlineScripts() {
    try {
      const pageUrl = location.href;
      let i = 0;
      document.querySelectorAll('script:not([src])').forEach((node) => {
        if (!isCapturableInlineScript(node)) return;
        const content = node.textContent || '';
        if (!content.trim()) return;
        emitInline(pageUrl, content, i);
        i += 1;
      });
      if (i > inlineSeq) inlineSeq = i;
    } catch (e) { /* DOM unavailable — skip */ }
  }

  // Every JS URL the page has ALREADY loaded: `<script src>` still in the DOM plus the
  // resource-timing timeline, which also surfaces the module / import() / fetch-loaded chunks
  // that BOTH the webRequest `types:["script"]` filter and the MutationObserver miss.
  function collectLoadedScriptUrls() {
    const urls = new Set();
    try {
      document.querySelectorAll('script[src]').forEach((s) => {
        if (s.src) urls.add(s.src);
      });
    } catch (e) { /* DOM unavailable — skip */ }
    try {
      for (const entry of performance.getEntriesByType('resource')) {
        const name = entry.name || '';
        if (entry.initiatorType === 'script' || /\.m?js(\?|#|$)/i.test(name)) {
          urls.add(name);
        }
      }
    } catch (e) { /* Resource Timing unavailable — skip */ }
    return Array.from(urls);
  }

  // Report every already-loaded JS URL. The background gates on isCapturing/scope and dedups
  // by content hash, so re-reporting a URL webRequest already caught is harmless.
  function scanLoadedScripts() {
    for (const url of collectLoadedScriptUrls()) {
      emitScript(url, 'initial-scan');
    }
    scanInlineScripts();
  }

  // On a full load (and bfcache restore), scan — but only when capture is active, so an idle
  // extension never wakes its service worker on every page. `isCapturing` is read straight
  // from storage (no runtime message → no wakeup); the no-reload path (capture toggled on over
  // a loaded page) is driven separately by startCapture's explicit `rescanScripts` message.
  async function scanIfCapturing() {
    try {
      const { isCapturing } = await chrome.storage.local.get('isCapturing');
      if (isCapturing) scanLoadedScripts();
    } catch (e) { /* storage unavailable — skip */ }
  }

  if (document.readyState === 'complete') {
    scanIfCapturing();
  } else {
    window.addEventListener('load', scanIfCapturing, { once: true });
  }
  window.addEventListener('pageshow', (event) => { if (event.persisted) scanIfCapturing(); });

  const scriptObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (!node || node.tagName !== 'SCRIPT') return;
        if (node.src) {
          emitScript(node.src, 'dynamic-script');
        } else if (isCapturableInlineScript(node)) {
          // Inline <script> injected after load (SPA hydration/route render) — DEBT D45a.
          const content = node.textContent || '';
          if (content.trim()) emitInline(location.href, content, inlineSeq++);
        }
      });
    });
  });

  scriptObserver.observe(document.documentElement, {
    childList: true,
    subtree: true
  });

  // Relay RESPONSE bodies from the opt-in main-world hook (DEBT D45b2). The hook (inject/xhr-hook.js)
  // runs in the page world and posts via window.postMessage; validate the source tag + that it came
  // from THIS window, then forward to the background (which re-applies scope + caps + redaction +
  // dedup). Page-forgeable by design (shared world) — the platform tags these findings lower-trust.
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.source !== 'recon-xhr-hook' || typeof d.url !== 'string' || typeof d.body !== 'string') return;
    try {
      const sent = chrome.runtime.sendMessage({
        action: 'responseBodyObserved',
        method: d.method,
        url: d.url,
        status: d.status,
        contentType: d.contentType,
        body: d.body,
        pageUrl: location.href
      });
      if (sent && typeof sent.catch === 'function') sent.catch(() => {});
    } catch (e) {
      // Extension context torn down — drop; the next response will retry.
    }
  });
})();
