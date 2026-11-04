// Who may send which message to the background worker.
//
// The content script runs on <all_urls> in every frame, and Chrome's guidance is to "assume that
// messages from a content script might have been crafted by an attacker"
// (developer.chrome.com/docs/extensions/develop/concepts/messaging). Without this check a
// compromised page could send `updateSettings` (point uploads + the Bearer token at its own
// server) or `getExportData` (read every captured file and its auth headers). Storage, the other
// way to reach that state, is closed in modules/storage-access.js.

// Notices the content script sends from a page. Every other action is a popup command.
export const PAGE_ACTIONS = new Set(['dynamicScriptDetected', 'inlineScriptDetected', 'responseBodyObserved']);

// True when `sender` may send `action`. A popup command must come from one of this extension's
// own pages (checked by URL, not by "has no tab": the popup can also be opened in a tab). A page
// notice must come from a sender in a tab — Chrome only sets `sender.tab` for tab frames, so a
// web frame embedded in a non-tab surface (another extension's popup, a side panel) is not
// captured; `<script src>` loads there are still captured through webRequest.
export function isAllowedSender(action, sender, runtime) {
  if (!sender || sender.id !== runtime.id) return false;
  if (PAGE_ACTIONS.has(action)) return Boolean(sender.tab);
  return typeof sender.url === 'string' && sender.url.startsWith(runtime.getURL(''));
}

// The onMessage listener's first step. True = hand the message to its handler. False = rejected:
// no handler runs, and a sender that waits for a reply gets {success:false, error:'forbidden'}.
export function admitMessage(request, sender, runtime, sendResponse) {
  const action = request && request.action;
  if (isAllowedSender(action, sender, runtime)) return true;
  console.warn('[recon] rejected message from an unexpected sender', action, sender && sender.url);
  if (!PAGE_ACTIONS.has(action)) sendResponse({ success: false, error: 'forbidden' });
  return false;
}
