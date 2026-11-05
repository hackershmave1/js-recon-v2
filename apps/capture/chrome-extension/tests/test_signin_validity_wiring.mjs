// Sign-in validity wiring: the popup must show "sign in" BEFORE a capture starts when the token is
// expired (locally known) or rejected (the workspace said 401), never a signed-in UI whose first
// upload then fails. A running capture must stay stoppable from the sign-in screen, and a worker
// respawn with a known-expired token must not send doomed uploads (each one re-notified).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { listProjectsWithCache } from '../modules/projects-cache.js';

const pass = (name) => console.log(`✓ ${name}`);
const here = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => fs.readFileSync(path.resolve(here, p), 'utf8');

// --- uploader: pause up front, without a doomed request or a notification --------------------
const uploaderSource = read('../modules/batch-uploader.js').replace('export class BatchUploader', 'class BatchUploader');
let fetches = 0;
const sandbox = { console, URL, setTimeout, clearTimeout, AbortController, fetch: async () => { fetches += 1; return { ok: true, json: async () => ({}) }; } };
vm.createContext(sandbox);
vm.runInContext(`${uploaderSource}\nthis.BatchUploader = BatchUploader;`, sandbox);
const up = new sandbox.BatchUploader();
up.setEndpoint('http://localhost:8000');
let notified = 0;
up.setOnAuthFailure(() => { notified += 1; });
up.pauseForAuth();
up.pendingQueue.push({ url: 'https://acme.io/a.js', contentHash: 'h', sessionId: 's', contentLength: 2, content: 'x;' });
await up.processBatch();
assert.equal(fetches, 0, 'paused: no upload sent');
assert.equal(notified, 0, 'pausing up front is not an auth failure');
assert.equal(up.getStats().authPaused, true);
up.resumeUploads();
await new Promise((r) => setTimeout(r, 20));
assert.equal(fetches, 1, 're-login resumes the drain');
pass('uploader.pauseForAuth holds the outbox silently; resumeUploads drains it');

// --- projects cache passes the live status through (the workspace's 401 verdict) --------------
const mem = { data: {}, async get(k) { return Object.fromEntries(k.map((x) => [x, this.data[x]])); }, async set(o) { Object.assign(this.data, o); } };
const rejected = await listProjectsWithCache(async () => ({ success: false, status: 401 }), mem);
assert.equal(rejected.status, 401);
assert.equal((await listProjectsWithCache(async () => ({ success: true, projects: [] }), mem)).status, undefined);
pass('listProjectsWithCache surfaces the live failure status (still falls back to the cache)');

// --- background wiring (background.js is not importable in Node) -----------------------------
const bg = read('../background.js');
const status = bg.slice(bg.indexOf('  getStatus(sendResponse) {'), bg.indexOf('  getStatus(sendResponse) {') + 2500);
assert.match(status, /authMarker\(authToken, \{ trustLocalExpiry: !this\.settings\?\.localExpiryUntrusted \}\)/, 'null-safe before settings load');
assert.match(status, /authExpired: marker\.authExpired/, 'authExpired is a top-level status field, never a setting');
const init = bg.slice(bg.indexOf('  async initialize() {'), bg.indexOf('this.session.restore()'));
assert.match(init, /if \(authMarker\(this\.settings\.authToken, \{ trustLocalExpiry: !this\.settings\.localExpiryUntrusted \}\)\.authExpired\) \{\s*this\.batchUploader\.pauseForAuth\(\);\s*await this\.notifyExpiredOncePerToken\(\);/);
const notify = bg.slice(bg.indexOf('  async notifyExpiredOncePerToken() {'), bg.indexOf('  async login('));
assert.match(notify, /if \(authNotifiedExp === exp\) return;[\s\S]*this\.handleAuthExpired\('expired'\);/, 'one notification per token across respawns');
const login = bg.slice(bg.indexOf('  async login('), bg.indexOf('  async logout('));
assert.match(login, /localExpiryUntrusted: authMarker\(result\.token\)\.authExpired/, 'a token expired on arrival means the clock is skewed: stop trusting the local read');
const projects = bg.slice(bg.indexOf('  async listProjects(sendResponse) {'), bg.indexOf('  getExportData('));
assert.match(projects, /const tokenAtSend = this\.settings\?\.authToken;/);
assert.match(projects, /authRejected: status === 401 && !!tokenAtSend && this\.settings\?\.authToken === tokenAtSend/,
  'only a 401 for the token still held (a 403 is a proxy, not our auth)');
pass('background: marker + expired flag, pause on start, skew distrust, guarded 401 verdict');

// --- popup wiring ------------------------------------------------------------------------------
const app = read('../src/popup/app.jsx');
assert.match(app, /if \(!settings\.authToken \|\| forceLogin \|\| status\.authExpired\) \{/);
assert.match(app, /api\.listProjects\(\)\.then\(\(res\) => \{[^}]*if \(res\?\.authRejected\) setForceLogin\(true\);/);
assert.match(app, /expired: !!status\.authExpired \|\| forceLogin/);
assert.match(app, /capture: status\.isCapturing \? \{ held: status\.uploader\?\.pendingQueueLength \|\| 0, stop: toggleCapture \} : null/, 'files still to upload, not every captured file');
const signIn = app.slice(app.indexOf('  async function signIn('), app.indexOf('  async function signOut('));
assert.match(signIn, /setForceLogin\(false\);[\s\S]*setStatus\(\(s\) => \(\{ \.\.\.s, authExpired: false \}\)\);/, 'sign-in leaves the gate at once, not on the next 2s poll');
const login_view = read('../src/popup/components/LoginView.jsx');
assert.match(login_view, /vm\.expired &&/);
assert.match(login_view, /vm\.capture &&[\s\S]*onClick=\{vm\.capture\.stop\}/, 'a running capture can be stopped from the sign-in screen');
pass('popup: gate on expired/rejected, expired note, Stop for a running capture');
