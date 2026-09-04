// D43d — in processFile the durable OUTBOX write (batchUploader.enqueue) must persist BEFORE the
// durable dedup write (dedupStore.put). Otherwise a worker teardown in the gap marks a file "seen"
// yet never queues it for upload — a permanent silent drop. background.js is chrome/DOM-coupled and
// not importable in Node (see test_background_engagement_wiring.mjs), so pin the order structurally.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// Both call sites are unique to processFile (enqueue(fileObject) once; dedupStore.put(contentHash
// once — the other dedupStore calls are delete/clear/getAll), so a global order check is sound.
const enqueueIdx = bg.indexOf('this.batchUploader.enqueue(fileObject)');
const dedupPutIdx = bg.indexOf('this.dedupStore.put(contentHash');
assert.ok(enqueueIdx >= 0, 'processFile enqueues the file to the durable outbox');
assert.ok(dedupPutIdx >= 0, 'processFile persists the durable dedup entry');
assert.ok(enqueueIdx < dedupPutIdx, 'outbox enqueue must precede the dedup put (DEBT D43d)');

// The in-memory dedup guard must still be set (it, not the durable put, blocks same-session
// re-capture), so the reorder cannot cause a same-session re-upload loop.
assert.match(bg, /this\.capturedHashes\.set\(contentHash/, 'in-memory dedup guard is still set');

console.log('test_capture_outbox_ordering: ok');
