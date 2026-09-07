// D46(c): structural test — verifies the capture history wiring in background.js.
// Pattern-B: reads background.js as a string and asserts on structural invariants.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// Storage key and cap constant exist.
assert.ok(/CAPTURE_HISTORY_KEY\s*=\s*'captureHistory'/.test(bg), 'declares CAPTURE_HISTORY_KEY');
assert.ok(/HISTORY_MAX\s*=\s*10/.test(bg), 'declares HISTORY_MAX = 10');

// The worker caches the last findings summary so history can persist it before rotation.
assert.ok(bg.includes('this._lastFindingsSummary = null'), 'initialises _lastFindingsSummary to null');
assert.ok(
  /getSessionFindingsSummary[\s\S]{0,300}this\._lastFindingsSummary\s*=\s*result/.test(bg),
  'getSessionFindingsSummary handler caches a complete summary'
);

// _saveSessionToHistory method: exists, reads capturedFiles, writes to storage.
assert.ok(bg.includes('async _saveSessionToHistory()'), 'declares _saveSessionToHistory');
// These invariants must all appear somewhere in the method body.
assert.ok(bg.includes("chrome.storage.local.get(CAPTURE_HISTORY_KEY)"), '_saveSessionToHistory reads from storage');
assert.ok(bg.includes("chrome.storage.local.set({ [CAPTURE_HISTORY_KEY]: history })"), '_saveSessionToHistory writes history to storage');
assert.ok(bg.includes('history.length > HISTORY_MAX'), '_saveSessionToHistory trims history to cap');

// getHistory method: exists and reads CAPTURE_HISTORY_KEY.
assert.ok(bg.includes('async getHistory(sendResponse)'), 'declares getHistory method');
assert.ok(
  /getHistory[\s\S]{0,200}chrome\.storage\.local\.get\(CAPTURE_HISTORY_KEY\)/.test(bg),
  'getHistory reads from storage'
);

// handleMessage wires getHistory.
assert.ok(/getHistory:\s*async/.test(bg), 'handleMessage registers getHistory');

// newSession saves history before rotating.
assert.ok(
  /await this\._saveSessionToHistory\(\);[\s\S]{0,100}this\._lastFindingsSummary\s*=\s*null/.test(bg),
  'newSession saves to history and clears the summary cache before rotating'
);
assert.ok(
  /await this\._saveSessionToHistory\(\);[\s\S]{0,300}this\.sessionId\s*=\s*await this\.sessionStore\.rotate\(\)/.test(bg),
  'newSession calls _saveSessionToHistory before rotating the session id'
);

console.log('ok - D46(c) capture history wiring');
