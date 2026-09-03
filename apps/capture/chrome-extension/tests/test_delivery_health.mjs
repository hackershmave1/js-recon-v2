// Unit tests for the pure delivery-health helper (modules/delivery-health.js). Pure module
// (no chrome/fetch/DOM), so imported directly. Covers the honest counts + fail>warn>ok
// precedence that the D42 popup strip and connection dot render.
import assert from 'node:assert/strict';
import { deriveDelivery, reasonText } from '../modules/delivery-health.js';

function statusWith({ uploader = {}, processingStats = {} } = {}) {
  return { uploader, processingStats };
}

function test_clean_state_is_ok() {
  const d = deriveDelivery(statusWith({ uploader: { uploadedFiles: 12, pendingQueueLength: 0, paired: true } }));
  assert.equal(d.health, 'ok', 'uploads flowing, nothing failed => ok');
  assert.equal(d.uploaded, 12);
  assert.equal(d.failedTotal, 0);
  assert.equal(d.paired, true);
}

function test_skips_only_are_warn() {
  // Per-file processing failures (fetch/decompress/oversize) are soft — a warn, not a hard fail.
  const d = deriveDelivery(statusWith({
    uploader: { uploadedFiles: 5, paired: true },
    processingStats: { failedFiles: 3, lastFailureReason: 'asset_too_large', lastFailureUrl: 'https://x/app.js' }
  }));
  assert.equal(d.health, 'warn', 'skips alone => warn');
  assert.equal(d.skipped, 3);
  assert.equal(d.failedTotal, 3);
  assert.equal(d.lastReason, 'file over size cap', 'reason code mapped to friendly text');
  assert.equal(d.lastUrl, 'https://x/app.js');
}

function test_upload_error_is_fail() {
  const d = deriveDelivery(statusWith({ uploader: { uploadedFiles: 1, lastError: 'response exceeds 10485760 bytes' } }));
  assert.equal(d.health, 'fail', 'a live upload error => fail');
  assert.equal(d.lastReason, 'response exceeds 10485760 bytes', 'upload error is the most actionable message');
}

function test_dropped_batch_is_fail() {
  const d = deriveDelivery(statusWith({ uploader: { uploadedFiles: 2, droppedFiles: 4, paired: true } }));
  assert.equal(d.health, 'fail', 'a permanently dropped batch => fail');
  assert.equal(d.dropped, 4);
  assert.equal(d.failedTotal, 4);
}

function test_unpaired_ack_is_fail() {
  // paired === false means the last ack landed in the shared tenant (bad/expired token) — the
  // silent data-loss D41 warns about; surface it as a hard fail.
  const d = deriveDelivery(statusWith({ uploader: { uploadedFiles: 3, paired: false } }));
  assert.equal(d.health, 'fail', 'unpaired ack => fail');
  assert.equal(d.paired, false);
}

function test_fail_outranks_warn() {
  const d = deriveDelivery(statusWith({
    uploader: { droppedFiles: 1, paired: true },
    processingStats: { failedFiles: 9 }
  }));
  assert.equal(d.health, 'fail', 'a hard fail signal outranks soft skips');
  assert.equal(d.failedTotal, 10, 'failedTotal sums dropped + skipped');
}

function test_paired_null_before_first_upload() {
  const d = deriveDelivery(statusWith({ uploader: { uploadedFiles: 0 } }));
  assert.equal(d.paired, null, 'no boolean paired yet => null, not false');
  assert.equal(d.health, 'ok', 'idle pre-first-upload is ok, not a failure');
}

function test_missing_status_never_throws() {
  for (const s of [undefined, null, {}, { uploader: null, processingStats: null }]) {
    const d = deriveDelivery(s);
    assert.equal(d.health, 'ok');
    assert.equal(d.uploaded, 0);
    assert.equal(d.failedTotal, 0);
    assert.equal(d.paired, null);
  }
}

function test_reason_text_passthrough() {
  assert.equal(reasonText('fetch_failed'), 'fetch failed');
  assert.equal(reasonText('decompress_failed'), 'decompress failed');
  assert.equal(reasonText(''), '', 'empty => empty');
  assert.equal(reasonText('some_unknown_code'), 'some_unknown_code', 'unknown code passes through raw');
}

const tests = [
  test_clean_state_is_ok,
  test_skips_only_are_warn,
  test_upload_error_is_fail,
  test_dropped_batch_is_fail,
  test_unpaired_ack_is_fail,
  test_fail_outranks_warn,
  test_paired_null_before_first_upload,
  test_missing_status_never_throws,
  test_reason_text_passthrough
];

let passed = 0;
for (const t of tests) { t(); passed += 1; }
console.log(`delivery-health: ${passed}/${tests.length} passed`);
