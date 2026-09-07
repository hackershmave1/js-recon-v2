// D45b2 — request/response body capture wiring. background.js / content-script.js / the injected
// hook / manifest / popup are chrome/DOM/JSX-coupled, so these are structural source assertions.
// The decode/redact/cap logic is covered behaviourally in test_body_capture.mjs.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const R = (p) => fs.readFileSync(path.resolve(__dirname, p), 'utf8');
const bg = R('../background.js');
const cs = R('../content-script.js');
const hook = R('../inject/xhr-hook.js');
const manifest = JSON.parse(R('../manifest.json'));
const app = R('../src/popup/app.jsx');
const settingsVm = R('../src/popup/viewmodels/settingsVm.js');
const settingsView = R('../src/popup/components/SettingsView.jsx');

// --- request bodies (ON by default): webRequest onBeforeRequest, NO main world ---
assert.match(bg, /onBeforeRequest\.addListener\([\s\S]*?captureRequestBody\(details\)[\s\S]*?\["requestBody"\]/, 'onBeforeRequest captures request bodies');
assert.match(bg, /captureRequestBody\(details\)\s*\{/, 'has captureRequestBody');
assert.match(bg, /prepareRequestBody\(details\.requestBody, REQ_BODY_CAP\)/, 'request body is decoded + redacted + capped');
assert.match(bg, /obs\.reqBody = reqBody/, 'the observation carries the redacted request body');
assert.match(bg, /import \{ prepareRequestBody, redactBody, capBody \} from '\.\/modules\/body-capture\.js'/, 'imports body-capture');
assert.ok(bg.includes('BODY_TOTAL_CAP'), 'bodies bounded by a per-session total budget');
// review fixes: the pending request body is reclaimed BEFORE any early-return (no orphan on a
// mid-flight capture stop), stopCapture clears the pending map, and persistence strips bodies.
assert.match(bg, /recordObservation\(details\)\s*\{[\s\S]*?pendingRequestBodies\.delete\(details\.requestId\)[\s\S]*?if \(!this\.isCapturing\) return;/, 'request body reclaimed before the isCapturing early-return (Finding 3)');
assert.match(bg, /async stopCapture[\s\S]*?this\.pendingRequestBodies\.clear\(\)/, 'stopCapture clears pending request bodies (Finding 3)');
assert.match(bg, /this\.observations\.map\(\(o\) => \(\{ method: o\.method, url: o\.url \}\)\)/, 'persisted observations are lean — no bodies to disk (Finding 2)');

// --- response bodies (OPT-IN, off by default): main-world hook via chrome.scripting ---
assert.match(bg, /handleResponseBody\(request\)\s*\{/, 'has handleResponseBody');
assert.match(bg, /captureResponseBodies !== true/, 'response bodies gated on the opt-in setting');
assert.match(bg, /registerContentScripts\(\[\{[\s\S]*?inject\/xhr-hook\.js/, 'registers the response-body hook dynamically');
assert.match(bg, /world: 'MAIN'/, 'the hook is registered in the MAIN world');
assert.match(bg, /unregisterContentScripts\(\{ ids: \['recon-xhr-hook'\] \}\)/, 'unregisters when the toggle is off');
assert.match(bg, /capBody\(redactBody\(request\.body\), RESP_BODY_CAP\)/, 'response body redacted + capped');
assert.match(bg, /captureResponseBodies: result\.captureResponseBodies === true/, 'captureResponseBodies defaults OFF (opt-in)');

// --- content-script relays the hook's postMessage ---
assert.match(cs, /addEventListener\('message'/, 'content-script listens for the main-world hook');
assert.match(cs, /d\.source !== 'recon-xhr-hook'/, 'validates the hook source tag + same-window');
assert.match(cs, /action: 'responseBodyObserved'/, 'relays response bodies to the background');

// --- the injected main-world hook wraps fetch + XHR and posts bodies ---
assert.match(hook, /window\.fetch =/, 'hook wraps fetch');
assert.match(hook, /XHR\.prototype\.(open|send) =/, 'hook wraps XMLHttpRequest');
assert.match(hook, /window\.postMessage/, 'hook posts to the content script');
assert.match(hook, /recon-xhr-hook/, 'hook uses the shared source tag');

// --- manifest: scripting permission for dynamic MAIN-world registration ---
assert.ok(manifest.permissions.includes('scripting'), 'manifest declares the scripting permission');

// --- popup: opt-in toggle wired end to end ---
assert.match(settingsVm, /toggleResponseBodies:.*patchSettings\(\{ captureResponseBodies:/, 'popup vm exposes the response-bodies toggle');
assert.match(settingsView, /vm\.toggleResponseBodies/, 'settings screen renders the toggle');
assert.match(settingsView, /vm\.captureResponseBodies/, 'settings screen reflects the toggle state');

console.log('test_body_capture_wiring: ok');
