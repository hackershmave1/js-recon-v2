// Review finding #3 — MV3 requires event listeners to be registered synchronously in
// the worker's first turn, and their handlers must defer state-dependent work until
// initialize() completes. This pins that structure so it can't silently regress back
// to post-await registration (which drops the event that woke the worker).
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// Bootstrap registers listeners synchronously and exposes the init promise as `ready`.
assert.match(bg, /extractor\.ready\s*=\s*extractor\.initialize\(\)/, 'bootstrap assigns ready = initialize()');
assert.match(bg, /\nextractor\.setupListeners\(\);/, 'bootstrap calls setupListeners() synchronously');

// initialize() must NOT register listeners itself (that would be after awaits = too late).
const initStart = bg.indexOf('async initialize()');
const initEnd = bg.indexOf('setupListeners() {');
assert.ok(initStart >= 0 && initEnd > initStart, 'found initialize() and setupListeners()');
assert.ok(!/this\.setupListeners\(\)/.test(bg.slice(initStart, initEnd)), 'initialize() must not register listeners (MV3: dropped wake events)');

// Handlers gate their state-using work on this.ready.
assert.match(bg, /onCompleted\.addListener\([\s\S]*?this\.ready\.then\(\(\)\s*=>\s*this\.handleRequest/, 'onCompleted handler gates on ready');
assert.match(bg, /onMessage\.addListener\([\s\S]*?this\.ready\.then\(\(\)\s*=>\s*this\.handleMessage/, 'onMessage handler gates on ready');
assert.match(bg, /onAlarm\.addListener\([\s\S]*?this\.ready\.then\(/, 'onAlarm handler gates on ready');

// Auth-context capture must only run while capturing (the gate moved to the call site
// when captureRequestAuthContext was extracted into AuthContextTracker).
assert.match(bg, /onBeforeSendHeaders\.addListener\([\s\S]*?if \(this\.isCapturing\) this\.authTracker\.record/, 'onBeforeSendHeaders records auth context only while capturing');

console.log('test_mv3_listeners: ok');
