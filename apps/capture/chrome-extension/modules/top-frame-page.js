// top-frame-page.js — the page a script request belongs to (ADR-0010).
//
// Read from the TAB at admission, never from the request: Chrome's webRequest details have no
// `documentUrl` (they carry `initiator`, an origin, which a page can influence through redirects
// and sandboxed frames), and a page-supplied value would let a hostile page name its own scope.
// `tabs.get(tabId).url` is "the last committed URL of the main frame of the tab" (Chrome tabs API),
// so a script in a cross-origin iframe is judged by the TOP page, and an SPA that navigated after
// load is judged by where the tab is NOW. Each call reads the tab again: a cache would go stale on
// exactly that navigation.

// The hostname of the tab's main-frame page, or null when it cannot be determined: no tab
// (`tabId` -1: the request is from the worker or not tied to a tab), the tab is gone, the URL is not
// http(s), or Chrome withholds it.
export async function topFramePageHost(tabId, tabsApi) {
  if (!Number.isInteger(tabId) || tabId < 0) return null;
  try {
    const tab = await tabsApi.get(tabId);
    const url = tab && typeof tab.url === 'string' ? tab.url : '';
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.hostname.toLowerCase() || null;
  } catch (e) {
    return null;  // tabs.get rejects for a missing tab; an unparseable or empty URL lands here too
  }
}
