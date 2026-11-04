// Who may send which message to the background worker. Chrome: "Content scripts are less
// trustworthy than the extension service worker ... Assume that messages from a content script
// might have been crafted by an attacker" (developer.chrome.com/docs/extensions/develop/concepts/messaging).
// The content script runs on <all_urls> in every frame, so a popup-only command (change the
// workspace URL, export captured files) must never be accepted from a web page.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PAGE_ACTIONS, isAllowedSender } from '../modules/message-sender.js';

const ID = 'abcdefghijklmnopabcdefghijklmnop';
const runtime = { id: ID, getURL: (p) => `chrome-extension://${ID}/${p}` };
const popup = { id: ID, url: `chrome-extension://${ID}/popup.html` };
// The E2E recipe opens popup.html in a TAB and drives the worker from there: still the extension.
const popupInTab = { ...popup, tab: { id: 7 } };
const page = { id: ID, url: 'https://evil.example/', tab: { id: 3 }, frameId: 0 };
const pageFrame = { ...page, frameId: 12, url: 'https://ads.example/frame' };
const otherExtension = { id: 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz', url: 'chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/x.html' };

// Popup commands: only from the extension's own pages.
for (const action of ['updateSettings', 'getExportData', 'getStatus', 'login', 'newSession']) {
  assert.equal(isAllowedSender(action, popup, runtime), true, `${action} from popup`);
  assert.equal(isAllowedSender(action, popupInTab, runtime), true, `${action} from popup opened in a tab`);
  assert.equal(isAllowedSender(action, page, runtime), false, `${action} forged by a web page`);
  assert.equal(isAllowedSender(action, pageFrame, runtime), false, `${action} forged by an iframe`);
  assert.equal(isAllowedSender(action, otherExtension, runtime), false, `${action} from another extension`);
}

// Page notices: only from the content script running in a tab.
for (const action of PAGE_ACTIONS) {
  assert.equal(isAllowedSender(action, page, runtime), true, `${action} from a page`);
  assert.equal(isAllowedSender(action, pageFrame, runtime), true, `${action} from a frame`);
  assert.equal(isAllowedSender(action, popup, runtime), false, `${action} needs a tab`);
}

// Malformed input never throws and is never allowed.
assert.equal(isAllowedSender(undefined, popup, runtime), true); // unknown action: popup-gated, handler ignores it
assert.equal(isAllowedSender('getStatus', undefined, runtime), false);
assert.equal(isAllowedSender('getStatus', { id: ID }, runtime), false); // no url

// Wiring: the listener rejects BEFORE deferring to a handler, and FIRE_AND_FORGET is the same set.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
const listener = bg.slice(bg.indexOf('chrome.runtime.onMessage.addListener'), bg.indexOf('chrome.alarms.onAlarm'));
assert.ok(listener.indexOf('isAllowedSender(') >= 0, 'onMessage checks the sender');
assert.ok(listener.indexOf('isAllowedSender(') < listener.indexOf('this.ready.then'), 'sender is checked before any handler runs');
assert.match(bg, /const FIRE_AND_FORGET_ACTIONS = PAGE_ACTIONS;/, 'one list of page actions');

console.log('test_message_sender: ok');
