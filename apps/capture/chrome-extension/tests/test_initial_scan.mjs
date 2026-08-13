// The extension must capture JS a page loaded BEFORE capture was active — webRequest only sees
// NEW requests and nothing else re-reads a loaded page, so an already-open tab used to capture
// 0 scripts until the operator reloaded (found in QA against a live SPA). This pins the
// initial-scan wiring — content-script enumeration + the startCapture rescan — so that gap
// can't silently regress. Mirrors test_mv3_listeners.mjs (structural source assertions; the
// content script is DOM/`chrome`-coupled browser glue that can't be imported in Node).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cs = fs.readFileSync(path.resolve(__dirname, '../content-script.js'), 'utf8');
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// --- content-script: enumerate already-loaded JS from BOTH the DOM and the resource-timing
// timeline. The timeline is what surfaces module / import() / fetch-loaded chunks that the
// webRequest types:["script"] filter and the MutationObserver both miss. ------------------- //
assert.ok(cs.includes("querySelectorAll('script[src]')"), 'scan reads <script src> from the DOM');
assert.ok(cs.includes("getEntriesByType('resource')"), 'scan reads the resource-timing timeline');
assert.ok(cs.includes("initiatorType === 'script'"), 'scan keeps script-initiated resources');
assert.ok(/\.m\?js/.test(cs), 'scan also keeps .js/.mjs-named resources (non-script initiatorType)');
assert.ok(cs.includes("action: 'dynamicScriptDetected'"), 'scan reports on the existing capture channel');

// The load-time scan gates on persisted isCapturing read from STORAGE (no runtime message), so
// an idle extension never wakes its service worker on every page load.
assert.ok(cs.includes("storage.local.get('isCapturing')"), 'load-time scan gates on persisted isCapturing');
assert.ok(cs.includes("addEventListener('load'"), 'scan triggers on window load');

// The no-reload path: a rescanScripts message (sent by startCapture) re-runs the scan.
assert.ok(cs.includes("action === 'rescanScripts'"), 'content-script handles rescanScripts');

// --- background: turning capture on rescans the active tab, so an already-open page is captured
// without a reload; a fresh session does the same. ----------------------------------------- //
assert.match(
  bg,
  /startCapture\(sendResponse\)\s*\{[\s\S]*?this\.rescanActiveTab\(\);/,
  'startCapture rescans the active tab',
);
assert.match(
  bg,
  /async rescanActiveTab\(\)\s*\{[\s\S]*?tabs\.query\([\s\S]*?active:\s*true[\s\S]*?sendMessage\([\s\S]*?rescanScripts/,
  'rescanActiveTab messages the active tab to rescan',
);
assert.match(bg, /newSession[\s\S]*?if \(this\.isCapturing\) this\.rescanActiveTab\(\);/, 'newSession rescans when capturing');

console.log('test_initial_scan: ok');
