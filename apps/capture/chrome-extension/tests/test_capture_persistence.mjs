// The popup's file/map/secret counters read an in-memory Map (capturedFiles) that an MV3
// service-worker teardown wipes — so the counter reset to 0 after the worker slept, even though
// the session and its uploads survived (found in QA). This pins the durability wiring: a lean,
// content-free projection persisted to chrome.storage.local and rehydrated on initialize(), so
// that regression can't silently return. Structural (background.js is chrome/DOM-coupled and not
// importable in Node), mirroring test_mv3_listeners.mjs / test_initial_scan.mjs.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// A dedicated storage key + debounced writer + rehydrate/clear helpers exist.
assert.ok(/capturedMetaKey\s*=\s*'capturedFilesMeta'/.test(bg), 'declares the capturedFilesMeta storage key');
assert.ok(bg.includes('schedulePersistCapturedMeta'), 'has a debounced persist method');
assert.ok(bg.includes('rehydrateCapturedFilesMeta'), 'has a rehydrate method');
assert.ok(bg.includes('_projectCapturedFile'), 'projects a lean per-file shape');

// The projection must be CONTENT-FREE: never persist raw file content or the sourcemap blob to
// chrome.storage.local (that is the uploader/outbox's job; storage has a quota).
const projMatch = bg.match(/_projectCapturedFile\(f\)\s*{[\s\S]*?return\s*{([\s\S]*?)};/);
assert.ok(projMatch, 'projection returns an object literal');
const projBody = projMatch[1];
assert.ok(!/\bcontent\b\s*:/.test(projBody), 'projection does not persist raw content');
assert.ok(!/sourceMapContent/.test(projBody), 'projection does not persist the sourcemap blob');
assert.ok(/contentHash/.test(projBody) && /secretCount/.test(projBody), 'projection keeps the counter fields');

// Cold start rehydrates AFTER the dedup set (both restore session-scoped state, dedup first so a
// rehydrated file is never re-fetched).
assert.ok(/rehydrateDedup\(\);[\s\S]{0,200}rehydrateCapturedFilesMeta\(\)/.test(bg),
  'initialize() rehydrates the counter after the dedup set');

// A capture schedules a persist; a session reset clears the persisted projection.
assert.ok(/capturedFiles\.set\(url, fileObject\);[\s\S]{0,700}schedulePersistCapturedMeta\(\)/.test(bg),
  'processFile schedules a persist after recording a file');
assert.ok((bg.match(/this\.clearCapturedFilesMeta\(\)/g) || []).length >= 2,
  'clearFiles and newSession both clear the persisted projection');

// Rehydrate must not clobber a live capture that beat it (only fill urls not already present).
assert.ok(/!this\.capturedFiles\.has\(f\.url\)/.test(bg), 'rehydrate only fills urls not already present');

// getFiles tolerates a rehydrated lean object (which has no dependencies array).
assert.ok(/dependencyCount:\s*Array\.isArray\(f\.dependencies\)/.test(bg),
  'getFiles derives dependencyCount defensively for lean rehydrated objects');

console.log('ok - capture-counter persistence wiring');
