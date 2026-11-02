// Structural pins for the "engagement funnel" worker wiring. background.js is chrome/DOM-coupled
// and not importable in Node (see test_capture_persistence.mjs), so — like test_mv3_listeners.mjs —
// this asserts the wiring exists in source so it can't silently regress. The behavioural logic that
// CAN run in Node is covered by runtime tests (test_active_engagement_reconcile.mjs,
// test_t007_batch_uploader_payload.mjs getStats).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// getStatus surfaces the active engagement (projectId + derived standalone) from the uploader's
// live binding, so the popup can display + restore it on every open.
assert.match(bg, /const uploaderStats = this\.batchUploader\.getStats\(\);/, 'getStatus reads uploader stats once');
assert.match(bg, /projectId:\s*uploaderStats\.projectId\s*\|\|\s*null,/, 'getStatus returns projectId');
assert.match(bg, /standalone:\s*!uploaderStats\.projectId,/, 'getStatus returns derived standalone');

// The reset helper delegates the "unbind" (rotate, drop captured state, clear the live + persisted
// binding, optionally drop the outbox) to CaptureSession.startStandalone — whose ordering is tested
// at runtime in test_capture_session.mjs.
const resetStart = bg.indexOf('async resetCaptureSession(');
assert.ok(resetStart >= 0, 'resetCaptureSession() is defined');
const resetBody = bg.slice(resetStart, resetStart + 600);
assert.match(resetBody, /await this\.session\.startStandalone\(\{\s*dropOutbox\s*\}\)/, 'reset delegates to CaptureSession.startStandalone, passing dropOutbox through');

// newSession hands the engagement binding + capture-gate patch to startNew in one call, switching
// the in-memory gate via onRotated (same turn as the rotation), then re-syncs the uploader.
const newStart = bg.indexOf('async newSession(');
const newBody = bg.slice(newStart, newStart + 3000);
assert.match(newBody, /await this\.session\.startNew\(\{\s*binding: \{ projectId, captureConfig, overrideKeys \},\s*persistWith: patch,\s*onRotated: \(\) => Object\.assign\(this\.settings, patch\)\s*\}\)/,
  'newSession passes binding, persistWith and the onRotated gate switch');
assert.match(newBody, /startNew\([\s\S]*?this\.batchUploader\.setScope\([\s\S]*?this\.batchUploader\.setPerformAnalysisOnUpload\(/, 'uploader scope + analyze flag re-synced after the transition');

// clearFiles refreshes the badge only after the counters reset (else it stays orange on stale failures).
assert.match(bg, /async clearFiles\(sendResponse\) \{\s*await this\.session\.clearCaptured\(\);[\s\S]{0,80}this\.updateBadge\(\);/, 'clearFiles updates the badge after clearCaptured');

// Logout clears the token + identity but KEEPS authTenantId (so a later different-tenant login is
// still detected), and resets to a fresh Standalone session with capture stopped (capture must not
// run behind the sign-in gate).
const logoutStart = bg.indexOf('async logout(');
assert.ok(logoutStart >= 0, 'logout() is defined');
const logoutBody = bg.slice(logoutStart, logoutStart + 1200);
assert.match(logoutBody, /authToken:\s*''/, 'logout clears the auth token');
assert.doesNotMatch(logoutBody, /authTenantId:\s*''/, 'logout does NOT clear authTenantId (kept so a later tenant-change login is still detected)');
assert.match(logoutBody, /this\.resetCaptureSession\(\{\s*stopCapturing:\s*true\s*\}\)/, 'logout resets + stops capture');

// Login records the tenant id and resets the session ONLY when the tenant changed (cross-tenant
// projectId stamping guard); a same-tenant re-login leaves the in-progress session intact.
const loginStart = bg.indexOf('async login(');
assert.ok(loginStart >= 0, 'login() is defined');
const loginBody = bg.slice(loginStart, loginStart + 2600);
assert.match(loginBody, /authTenantId:\s*nextTenantId/, 'login persists the new tenant id');
assert.match(loginBody, /prevTenantId && nextTenantId && prevTenantId !== nextTenantId/, 'login gates the reset on a tenant change');
assert.match(loginBody, /this\.resetCaptureSession\(\{\s*stopCapturing:\s*false,\s*dropOutbox:\s*true\s*\}\)/, 'login resets + drops the old-tenant outbox on a tenant change');
// Order matters (tenant isolation): the tenant-change reset — which clears the token and drops the
// outbox — MUST run BEFORE the new token is installed, or tenant A's queued files could flush under
// tenant B during the reset's awaits. Pin the order structurally.
const resetIdx = loginBody.indexOf('resetCaptureSession({ stopCapturing: false, dropOutbox: true })');
const installIdx = loginBody.indexOf('this.batchUploader.setAuthToken(this.settings.authToken)');
assert.ok(resetIdx >= 0 && installIdx >= 0 && resetIdx < installIdx, 'tenant-change reset precedes installing the new token');

// The tenant id must be whitelisted so the change-detection survives a worker respawn.
assert.match(bg, /'authTenantId',/, 'loadSettings whitelists authTenantId');

console.log('test_background_engagement_wiring: ok');
