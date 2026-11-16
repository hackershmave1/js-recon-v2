// The page a script belongs to comes from the TAB, never from the request (ADR-0010).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { topFramePageHost } from '../modules/top-frame-page.js';

const tabs = (map) => ({
  calls: [],
  async get(id) {
    this.calls.push(id);
    if (!(id in map)) throw new Error(`No tab with id: ${id}`);
    return map[id];
  },
});

// A normal tab: the host of its committed main-frame URL, lower-cased.
assert.equal(await topFramePageHost(7, tabs({ 7: { url: 'https://App.Acme.io/dashboard?x=1' } })), 'app.acme.io');

// tabId -1 (a worker or a request not tied to a tab) and non-integers: unknown, tabs.get never called.
const api = tabs({});
assert.equal(await topFramePageHost(-1, api), null);
assert.equal(await topFramePageHost(undefined, api), null);
assert.equal(await topFramePageHost(1.5, api), null);
assert.deepEqual(api.calls, []);

// A tab that is gone: tabs.get rejects, the page is unknown (not an error).
assert.equal(await topFramePageHost(99, tabs({})), null);

// A URL Chrome withholds, an empty URL, or a non-web page: unknown.
assert.equal(await topFramePageHost(1, tabs({ 1: {} })), null);
assert.equal(await topFramePageHost(1, tabs({ 1: { url: '' } })), null);
assert.equal(await topFramePageHost(1, tabs({ 1: { url: 'chrome://extensions' } })), null);
assert.equal(await topFramePageHost(1, tabs({ 1: { url: 'about:blank' } })), null);

// A cross-origin iframe's script is judged by the TOP page: the tab's URL is what is read, and a
// frame's own origin is not an input at all (the function takes only the tab id).
assert.equal(topFramePageHost.length, 2);
assert.equal(await topFramePageHost(3, tabs({ 3: { url: 'https://app.acme.io/' } })), 'app.acme.io');

// SPA navigation after load: each call re-reads the tab, so the page moves with the tab.
const moving = { 5: { url: 'https://app.acme.io/home' } };
const t = tabs(moving);
assert.equal(await topFramePageHost(5, t), 'app.acme.io');
moving[5] = { url: 'https://other-site.example/landing' };
assert.equal(await topFramePageHost(5, t), 'other-site.example');

// The module never reads a page-supplied or request-supplied value.
const source = readFileSync(new URL('../modules/top-frame-page.js', import.meta.url), 'utf8');
const code = source.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
assert.ok(!/documentUrl|initiator|referrer|location\./.test(code), 'only tabs.get feeds the page');

console.log('ok - top-frame page: from the tab, never the request');
