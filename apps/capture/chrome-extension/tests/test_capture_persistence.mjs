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

// The persisted projection itself (content-free, rehydrate fills only missing urls, cleared on
// every session transition) lives in CaptureSession and is tested at runtime in
// test_capture_session.mjs. What stays here is the capture pipeline's side of the wiring.

// A capture schedules a persist; a session reset clears the persisted projection. (Window widened
// for the D43d reorder, which added the outbox-before-dedup enqueue + explanatory comments between
// recording the file and scheduling the persist; still bounded so the two stay in the same region.)
assert.ok(/capturedFiles\.set\(url, fileObject\);[\s\S]{0,1400}this\.session\.schedulePersistCapturedMeta\(\)/.test(bg),
  'processFile schedules a persist after recording a file');

// getFiles tolerates a rehydrated lean object (which has no dependencies array).
assert.ok(/dependencyCount:\s*Array\.isArray\(f\.dependencies\)/.test(bg),
  'getFiles derives dependencyCount defensively for lean rehydrated objects');

console.log('ok - capture-counter persistence wiring');
