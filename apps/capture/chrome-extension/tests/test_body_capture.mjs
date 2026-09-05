// D45b2 — request/response body decode + credential redaction + caps (pure module).
import assert from 'node:assert';
import { decodeRequestBody, redactBody, capBody, prepareRequestBody } from '../modules/body-capture.js';

const enc = (s) => new TextEncoder().encode(s).buffer;

// --- decodeRequestBody: raw JSON bytes ---
assert.deepEqual(
  decodeRequestBody({ raw: [{ bytes: enc('{"a":1}') }] }),
  { text: '{"a":1}', type: 'raw' },
);
// multi-chunk raw
assert.deepEqual(
  decodeRequestBody({ raw: [{ bytes: enc('{"a":') }, { bytes: enc('1}') }] }),
  { text: '{"a":1}', type: 'raw' },
);
// --- decodeRequestBody: form ---
assert.deepEqual(
  decodeRequestBody({ formData: { user: ['bob'], q: ['x'] } }),
  { text: 'user=bob&q=x', type: 'form' },
);
// nothing usable → null
assert.equal(decodeRequestBody(null), null);
assert.equal(decodeRequestBody({}), null);
assert.equal(decodeRequestBody({ raw: [] }), null);

// --- redactBody: strip credential VALUES, keep shape ---
assert.match(redactBody('{"user":"bob","password":"hunter2"}'), /"user":"bob"/, 'non-cred kept');
assert.doesNotMatch(redactBody('{"user":"bob","password":"hunter2"}'), /hunter2/, 'password value redacted');
assert.doesNotMatch(redactBody('{"access_token":"abc.def.ghi"}'), /abc\.def\.ghi/, 'access_token redacted');
assert.doesNotMatch(redactBody('username=bob&password=hunter2'), /hunter2/, 'form password redacted');
assert.match(redactBody('username=bob&password=hunter2'), /username=bob/, 'form non-cred kept');
// GraphQL query with variables carrying a token
assert.doesNotMatch(redactBody('{"query":"mutation{login}","variables":{"secret":"s3cr3t"}}'), /s3cr3t/, 'nested secret redacted');
assert.equal(redactBody(''), '');
assert.equal(redactBody(null), '');

// --- capBody ---
assert.equal(capBody('abcdef', 3), 'abc');
assert.equal(capBody('ab', 10), 'ab');
assert.equal(capBody(null, 10), '');

// --- prepareRequestBody: decode → redact → cap ---
const prepared = prepareRequestBody({ raw: [{ bytes: enc('{"password":"hunter2","note":"keepme"}') }] }, 1000);
assert.ok(prepared && prepared.type === 'raw');
assert.doesNotMatch(prepared.text, /hunter2/, 'prepared body is redacted');
assert.match(prepared.text, /keepme/, 'prepared body keeps non-cred content');
assert.equal(prepareRequestBody({}, 1000), null);

// --- widened credential coverage (review Finding 1): bare token / jwt / bearer, no over-match ---
assert.doesNotMatch(redactBody('{"token":"abc123xyz"}'), /abc123xyz/, 'bare token redacted');
assert.doesNotMatch(redactBody('{"jwt":"eyJx.yZ.qq"}'), /eyJx\.yZ\.qq/, 'jwt redacted');
assert.doesNotMatch(redactBody('bearer=zzz9&x=1'), /zzz9/, 'bearer form field redacted');
assert.doesNotMatch(redactBody('{"otp":"123456","pin":"4242"}'), /123456|4242/, 'otp + pin redacted');
// A field that merely CONTAINS a credential word as a substring is NOT over-redacted.
assert.match(redactBody('{"tokenCount":5}'), /5/, 'tokenCount value kept (not a credential field)');

// --- decodeRequestBody honors maxChars (review Finding 4): bounds materialized text ---
assert.equal(decodeRequestBody({ raw: [{ bytes: enc('X'.repeat(100)) }] }, 10).text.length, 10, 'raw decode bounded to maxChars');
assert.ok(decodeRequestBody({ formData: { a: ['1'], b: ['2'], c: ['3'] } }, 4).text.length <= 4, 'form decode bounded to maxChars');

console.log('ok - body-capture decode/redact/cap (D45b2)');
