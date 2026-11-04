// Who may send which message to the background worker. Chrome: "Content scripts are less
// trustworthy than the extension service worker ... Assume that messages from a content script
// might have been crafted by an attacker" (developer.chrome.com/docs/extensions/develop/concepts/messaging).
// The content script runs on <all_urls> in every frame, so a popup-only command (change the
// workspace URL, export captured files) must never be accepted from a web page.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PAGE_ACTIONS, admitMessage, isAllowedSender } from '../modules/message-sender.js';

const ID = 'abcdefghijklmnopabcdefghijklmnop';
const OTHER = 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz';
const runtime = { id: ID, getURL: (p) => `chrome-extension://${ID}/${p}` };
const popup = { id: ID, url: `chrome-extension://${ID}/popup.html` };
// The E2E recipe opens popup.html in a TAB and drives the worker from there: still the extension.
const popupInTab = { ...popup, tab: { id: 7 } };
const page = { id: ID, url: 'https://evil.example/', tab: { id: 3 }, frameId: 0 };
const pageFrame = { ...page, frameId: 12, url: 'https://ads.example/frame' };
// Each check on its own: our id with a web URL (the content script); our URL with another id;
// our id with ANOTHER extension's URL; another extension's page in a tab.
const ourIdWebUrl = { id: ID, url: 'https://evil.example/' };
const otherIdOurUrl = { id: OTHER, url: `chrome-extension://${ID}/popup.html` };
const ourIdOtherExtensionUrl = { id: ID, url: `chrome-extension://${OTHER}/popup.html` };
const otherExtensionInTab = { id: OTHER, url: `chrome-extension://${OTHER}/x.html`, tab: { id: 9 } };

// Popup commands: only from the extension's own pages.
for (const action of ['updateSettings', 'getExportData', 'getStatus', 'login', 'newSession']) {
  assert.equal(isAllowedSender(action, popup, runtime), true, `${action} from popup`);
  assert.equal(isAllowedSender(action, popupInTab, runtime), true, `${action} from popup opened in a tab`);
  assert.equal(isAllowedSender(action, page, runtime), false, `${action} forged by a web page`);
  assert.equal(isAllowedSender(action, pageFrame, runtime), false, `${action} forged by an iframe`);
  assert.equal(isAllowedSender(action, ourIdWebUrl, runtime), false, `${action} from our id but a web URL`);
  assert.equal(isAllowedSender(action, otherIdOurUrl, runtime), false, `${action} from another id`);
  assert.equal(isAllowedSender(action, ourIdOtherExtensionUrl, runtime), false, `${action} from another extension's URL`);
}

// Page notices: only from a sender of THIS extension in a tab.
for (const action of PAGE_ACTIONS) {
  assert.equal(isAllowedSender(action, page, runtime), true, `${action} from a page`);
  assert.equal(isAllowedSender(action, pageFrame, runtime), true, `${action} from a frame`);
  assert.equal(isAllowedSender(action, popup, runtime), false, `${action} needs a tab`);
  assert.equal(isAllowedSender(action, otherExtensionInTab, runtime), false, `${action} from another extension`);
}

// Missing sender data is rejected, never thrown on.
assert.equal(isAllowedSender('getStatus', undefined, runtime), false);
assert.equal(isAllowedSender('getStatus', { id: ID }, runtime), false); // no url
// An unknown action from the popup is admitted (its handler simply ignores it).
assert.equal(isAllowedSender(undefined, popup, runtime), true);

// The listener's decision: a forged command never reaches a handler and gets `forbidden`;
// a forged page notice gets no reply (its sender does not wait for one).
function admit(request, sender) {
  const replies = [];
  const admitted = admitMessage(request, sender, runtime, (r) => replies.push(r));
  return { admitted, replies };
}
const warn = console.warn;
console.warn = () => {};
try {
  assert.deepEqual(admit({ action: 'updateSettings', settings: {} }, page), { admitted: false, replies: [{ success: false, error: 'forbidden' }] });
  assert.deepEqual(admit({ action: 'dynamicScriptDetected' }, popup), { admitted: false, replies: [] });
  assert.deepEqual(admit(null, page), { admitted: false, replies: [{ success: false, error: 'forbidden' }] });
  assert.deepEqual(admit({ action: 'getStatus' }, popup), { admitted: true, replies: [] });
  assert.deepEqual(admit({ action: 'inlineScriptDetected' }, pageFrame), { admitted: true, replies: [] });
} finally {
  console.warn = warn;
}

// Wiring: the listener's FIRST statement rejects with `return false` before any handler runs.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
const listener = bg.slice(bg.indexOf('chrome.runtime.onMessage.addListener'), bg.indexOf('chrome.runtime.onStartup'));
assert.match(
  listener,
  /\(request, sender, sendResponse\) => \{\s*(\/\/[^\n]*\n\s*)*if \(!admitMessage\(request, sender, chrome\.runtime, sendResponse\)\) return false;/,
  'the listener starts with the gate and returns false when it rejects'
);
assert.ok(listener.indexOf('admitMessage(') < listener.indexOf('this.ready.then'), 'gate runs before any handler');
assert.match(bg, /const FIRE_AND_FORGET_ACTIONS = PAGE_ACTIONS;/, 'one list of page actions');

// The rule relies on these manifest facts: no page can frame popup.html (no web_accessible_resources),
// and nothing else is an entry point (no externally_connectable, no external or port listeners).
const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../manifest.json'), 'utf8'));
assert.equal(manifest.web_accessible_resources, undefined, 'popup.html must not be web-accessible');
assert.equal(manifest.externally_connectable, undefined, 'no externally_connectable entry point');
assert.ok(!/onMessageExternal|onConnectExternal|runtime\.onConnect\b/.test(bg), 'no unchecked external/port entry points');

console.log('test_message_sender: ok');
