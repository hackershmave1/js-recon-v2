// Who may send which message to the background worker.
//
// The content script runs on <all_urls> in every frame, and Chrome's guidance is to "assume that
// messages from a content script might have been crafted by an attacker"
// (developer.chrome.com/docs/extensions/develop/concepts/messaging). Without this check a
// compromised page could send `updateSettings` (point uploads + the Bearer token at its own
// server) or `getExportData` (read every captured file and its auth headers).

// Notices the content script sends from a page. Every other action is a popup command.
export const PAGE_ACTIONS = new Set(['dynamicScriptDetected', 'inlineScriptDetected', 'responseBodyObserved']);

// True when `sender` may send `action`. A popup command must come from one of this extension's
// own pages (checked by URL, not by "has no tab": the popup can also be opened in a tab); a page
// notice must come from this extension's content script in a tab.
export function isAllowedSender(action, sender, runtime) {
  if (!sender || sender.id !== runtime.id) return false;
  if (PAGE_ACTIONS.has(action)) return Boolean(sender.tab);
  return typeof sender.url === 'string' && sender.url.startsWith(runtime.getURL(''));
}
