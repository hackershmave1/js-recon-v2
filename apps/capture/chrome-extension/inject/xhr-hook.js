(function () {
  'use strict';
  // Main-world RESPONSE-body hook (DEBT D45b2, OPT-IN, off by default). Registered dynamically via
  // chrome.scripting ONLY when captureResponseBodies is on, so the default build ships no
  // main-world code. Wraps fetch + XMLHttpRequest to observe API RESPONSE bodies and forward them
  // to the isolated content script via window.postMessage.
  //
  // Trust: this runs in the PAGE's world, so a hostile in-scope page can see AND forge these
  // messages (documented design risk — review Claim 1.3). The platform tags capture-sourced
  // findings as lower-trust, and this path is opt-in. Bounded here to API content-types + a size
  // cap so it can't flood the bridge.
  var TAG = 'recon-xhr-hook';
  var MAX = 256 * 1024;
  var API_CT = /(json|graphql|text\/plain|application\/xml|text\/xml)/i;

  function post(method, url, status, contentType, body) {
    try {
      window.postMessage(
        { source: TAG, method: method, url: url, status: status, contentType: contentType, body: body },
        '*'
      );
    } catch (e) { /* structured-clone / size failure — drop */ }
  }

  function wantsBody(contentType) {
    return typeof contentType === 'string' && API_CT.test(contentType);
  }

  // --- fetch ---
  var origFetch = window.fetch;
  if (typeof origFetch === 'function') {
    window.fetch = function () {
      var args = arguments;
      var p = origFetch.apply(this, args);
      try {
        p.then(function (resp) {
          try {
            var ct = resp && resp.headers && resp.headers.get ? resp.headers.get('content-type') : '';
            if (!wantsBody(ct)) return;
            var url = (resp && resp.url) || (typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '');
            var method = (args[1] && args[1].method) || (args[0] && args[0].method) || 'GET';
            resp.clone().text().then(function (text) {
              if (text && text.length <= MAX) post(method, url, resp.status, ct, text);
            }).catch(function () {});
          } catch (e) { /* ignore one response */ }
        }).catch(function () {});
      } catch (e) { /* ignore */ }
      return p;
    };
  }

  // --- XMLHttpRequest ---
  var XHR = window.XMLHttpRequest;
  if (XHR && XHR.prototype) {
    var origOpen = XHR.prototype.open;
    var origSend = XHR.prototype.send;
    XHR.prototype.open = function (method, url) {
      try { this.__recon_method = method; this.__recon_url = url; } catch (e) { /* frozen — ignore */ }
      return origOpen.apply(this, arguments);
    };
    XHR.prototype.send = function () {
      var xhr = this;
      try {
        xhr.addEventListener('load', function () {
          try {
            var ct = xhr.getResponseHeader && xhr.getResponseHeader('content-type');
            if (!wantsBody(ct)) return;
            // Only a text-ish responseType exposes responseText.
            if (xhr.responseType && xhr.responseType !== 'text') return;
            var text = xhr.responseText;
            if (text && text.length <= MAX) {
              post(xhr.__recon_method || 'GET', xhr.__recon_url || xhr.responseURL || '', xhr.status, ct, text);
            }
          } catch (e) { /* ignore one response */ }
        });
      } catch (e) { /* ignore */ }
      return origSend.apply(this, arguments);
    };
  }
})();
