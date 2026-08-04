(function() {
  'use strict';

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'fetchUrl') {
      fetchFromPage(request.url)
        .then(result => sendResponse(result))
        .catch(error => sendResponse({ success: false, error: error.message }));
      
      return true;
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

  const scriptObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node.tagName === 'SCRIPT' && node.src) {
          chrome.runtime.sendMessage({
            action: 'dynamicScriptDetected',
            url: node.src,
            timestamp: new Date().toISOString()
          });
        }
      });
    });
  });

  scriptObserver.observe(document.documentElement, {
    childList: true,
    subtree: true
  });
})();
