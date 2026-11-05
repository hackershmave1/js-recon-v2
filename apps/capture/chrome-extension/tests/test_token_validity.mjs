// modules/token-validity.js — the popup's sign-in gate used to trust a token's mere PRESENCE, so
// an 8h-old token looked signed in until the first upload 401'd mid-capture. The local expiry read
// must never lock a user out on its own: an unreadable token, or a client whose clock already
// reads a fresh token as expired, is "unknown" and left to the server's verdict.
import assert from 'node:assert/strict';
import { tokenExpiry, authMarker } from '../modules/token-validity.js';

const pass = (name) => console.log(`✓ ${name}`);
const b64url = (s) => Buffer.from(s).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const token = (payload) => `${b64url(JSON.stringify(payload))}.c2ln`;
const NOW = 1_800_000_000;

assert.equal(tokenExpiry(token({ sub: 'u', exp: NOW + 100 })), NOW + 100);
pass('reads exp from the payload');
// A payload whose base64url carries '-' and '_' (bytes 0xfb/0xff) must decode like the server's.
const tricky = token({ note: 'ûÿþ>?', exp: NOW + 5 });
assert.match(tricky.split('.')[0], /[-_]/);
assert.equal(tokenExpiry(tricky), NOW + 5);
pass('base64url alphabet (- and _) decodes');
for (const bad of ['', 'nodot', 'x.y', `${b64url('{"exp":"soon"}')}.s`, `${b64url('{"exp":true}')}.s`, `${b64url('[1]')}.s`]) {
  assert.equal(tokenExpiry(bad), null, bad);
}
pass('malformed / non-integer exp -> null');

assert.deepEqual(authMarker('', { nowSec: NOW }), { authToken: '', authExpired: false });
pass('no token -> signed out, not "expired"');
assert.deepEqual(authMarker(token({ exp: NOW + 3600 }), { nowSec: NOW }), { authToken: 'session', authExpired: false });
pass('valid token -> signed in (marker only, never the raw token)');
assert.deepEqual(authMarker(token({ exp: NOW - 1 }), { nowSec: NOW }), { authToken: '', authExpired: true });
assert.deepEqual(authMarker(token({ exp: NOW }), { nowSec: NOW }), { authToken: '', authExpired: true });
pass('expired (now >= exp, as the server checks) -> sign-in, flagged expired');
assert.deepEqual(authMarker(token({ exp: NOW + 30 }), { nowSec: NOW }), { authToken: '', authExpired: true });
pass('within the 60s skew -> treated as expired (early, the safe direction)');
assert.deepEqual(authMarker('not-a-token', { nowSec: NOW }), { authToken: 'session', authExpired: false });
pass('unreadable token -> unknown, the server decides (no local lockout)');
assert.deepEqual(authMarker(token({ exp: NOW - 1 }), { nowSec: NOW, trustLocalExpiry: false }), { authToken: 'session', authExpired: false });
pass('local expiry distrusted (clock skew seen at sign-in) -> the server decides');
