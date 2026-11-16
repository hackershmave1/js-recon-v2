// background.js admits scripts through the pure rule, reads the page from the tab, holds unknown
// hosts through the credential-less queue, and never uses `documentUrl` for the page (ADR-0010).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const bg = readFileSync(new URL('../background.js', import.meta.url), 'utf8');
const slice = (from, to) => bg.slice(bg.indexOf(from), bg.indexOf(to, bg.indexOf(from)));

const handle = slice('  async handleRequest(details) {', '  // The facts of one script request');
assert.match(handle, /const verdict = await this\.admitRequest\(details\);/);
assert.match(handle, /if \(verdict !== ADMIT\.CAPTURE\) \{\s*this\.handleNotCaptured\(details, verdict\);\s*return;/);
assert.ok(handle.indexOf('admitRequest') < handle.indexOf('this.shouldSkipUrl'), 'the rule runs before the capture path');
assert.match(handle, /isWorkspaceUrl\(details\.url\)\) return;/, 'the tool never recons itself');

const admit = slice('  async admitRequest(details) {', '  hostOf(url) {');
assert.match(admit, /topFramePageHost\(details\.tabId, chrome\.tabs\)/, 'the page comes from tabs.get');
assert.ok(!/documentUrl|details\.initiator/.test(admit), 'never the request\'s own claim about its page');
assert.match(admit, /ignored: this\.capture\.isIgnoredSite\(host\)/);

const notCaptured = slice('  handleNotCaptured(details, verdict) {', '  async processQueue()');
assert.match(notCaptured, /state\.newSites\.includes\(apexForHost\(host\)\)/, 'only a host on offer is held');
assert.match(notCaptured, /this\.quarantine\.enqueue\(/);
assert.ok(!/processingQueue/.test(notCaptured), 'a quarantined script never enters the capture queue');

assert.match(bg, /fetchContent: \(url\) => this\.contentFetcher\.fetchQuarantined\(url\)/);

// The manifest already grants what tabs.get needs for `url` (host permissions).
const manifest = JSON.parse(readFileSync(new URL('../manifest.json', import.meta.url), 'utf8'));
assert.ok(manifest.host_permissions.includes('<all_urls>'));

console.log('ok - background wiring: page from the tab, ordered rule, credential-less hold');
