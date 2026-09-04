// D41 — structural pins for the background wiring of auth-expiry handling. background.js is
// chrome/DOM-coupled and not importable in Node (see test_background_engagement_wiring.mjs), so pin
// the wiring in source; the behavioural logic runs in test_auth_expiry.mjs (the uploader).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// The uploader's auth-failure hook is wired to handleAuthExpired.
assert.match(bg, /this\.batchUploader\.setOnAuthFailure\(\(status\)\s*=>\s*this\.handleAuthExpired\(status\)\)/, 'setOnAuthFailure wired to handleAuthExpired');

// handleAuthExpired is defined, fires a single notification (authNotified guard) and refreshes badge.
const hStart = bg.indexOf('handleAuthExpired(status) {');
assert.ok(hStart >= 0, 'handleAuthExpired() is defined');
const hBody = bg.slice(hStart, hStart + 900);
assert.match(hBody, /if \(this\.authNotified\) return;/, 'handleAuthExpired fires once per episode');
assert.match(hBody, /this\.authNotified = true;/, 'handleAuthExpired latches the notification guard');
assert.match(hBody, /chrome\.notifications\.create/, 'handleAuthExpired notifies the operator');
assert.match(hBody, /this\.updateBadge\(\)/, 'handleAuthExpired refreshes the badge');

// login() success lifts the pause (resumeUploads) and re-arms the notification.
const loginStart = bg.indexOf('async login(');
assert.ok(loginStart >= 0, 'login() is defined');
const loginBody = bg.slice(loginStart, loginStart + 2800);
assert.match(loginBody, /this\.batchUploader\.resumeUploads\(\)/, 'login resumes the paused uploader');
assert.match(loginBody, /this\.authNotified = false;/, 'login re-arms the auth notification');
// resumeUploads must run AFTER the fresh token is installed, or it would drain under the old token.
const setTokenIdx = loginBody.indexOf('this.batchUploader.setAuthToken(this.settings.authToken)');
const resumeIdx = loginBody.indexOf('this.batchUploader.resumeUploads()');
assert.ok(setTokenIdx >= 0 && resumeIdx >= 0 && setTokenIdx < resumeIdx, 'resumeUploads runs after the new token is installed');

// The badge treats an auth-pause as unhealthy.
assert.match(bg, /up\.authPaused === true/, 'updateBadge marks an auth-pause as unhealthy');

console.log('test_auth_expiry_wiring: ok');
