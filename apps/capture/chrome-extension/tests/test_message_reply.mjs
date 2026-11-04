// modules/message-reply.js — the ONE popup<->worker wire shape (C7) and the reply dispatcher.
// Pure module, run directly in Node (like test_message_sender.mjs).
import assert from 'node:assert/strict';
import { toWire, replyOnce, dispatchReply } from '../modules/message-reply.js';

function pass(name) { console.log(`✓ ${name}`); }

// --- toWire: { ok, error?, ...detail } ------------------------------------------------------
assert.deepEqual(toWire({ success: true, sessionId: 's1' }), { sessionId: 's1', ok: true });
assert.deepEqual(toWire({ success: false, status: 401, error: 'bad credentials' }),
  { status: 401, error: 'bad credentials', ok: false });
// A handler that already speaks { ok } keeps its verdict; detail fields survive.
assert.deepEqual(toWire({ ok: false, error: 'timeout' }), { error: 'timeout', ok: false });
assert.deepEqual(toWire({ ok: true, status: 200, latencyMs: 12 }), { status: 200, latencyMs: 12, ok: true });
// A plain data reply (getStatus / getFiles / getHistory) is a success.
assert.deepEqual(toWire({ isCapturing: true, fileCount: 3 }), { isCapturing: true, fileCount: 3, ok: true });
assert.deepEqual(toWire(undefined), { ok: true });
// `ok` is written LAST: a handler's own `ok: true` can never override a `success: false`.
assert.deepEqual(toWire({ ok: true, success: false, error: 'x' }), { error: 'x', ok: false });
pass('toWire: one { ok, error? } shape, failures-as-values never read as success');

// --- replyOnce: one reply per message, always in wire shape ---------------------------------
{
  const sent = [];
  const reply = replyOnce((r) => sent.push(r));
  reply({ success: true });
  reply({ success: false, error: 'late' });
  assert.deepEqual(sent, [{ ok: true }]);
  pass('replyOnce: a second reply is dropped');
}

{
  // Chrome throws on a reply over its message-size limit (a huge export); the handler then sends
  // its own failure reply, which must not be swallowed as a duplicate.
  const sent = [];
  let first = true;
  const reply = replyOnce((r) => { if (first) { first = false; throw new Error('Message length exceeded'); } sent.push(r); });
  assert.throws(() => reply({ success: true, exportData: 'huge' }));
  reply({ success: false, error: 'Message length exceeded' });
  assert.deepEqual(sent, [{ error: 'Message length exceeded', ok: false }]);
  pass('replyOnce: a reply Chrome refused does not block the failure reply');
}

// --- dispatchReply: a throwing / rejecting handler still answers ----------------------------
{
  const sent = [];
  const reply = replyOnce((r) => sent.push(r));
  await dispatchReply(() => { throw new Error('boom'); }, reply);
  assert.deepEqual(sent, [{ ok: false, error: 'boom' }]);
  pass('dispatchReply: a sync throw becomes { ok: false, error }');
}
{
  const sent = [];
  const reply = replyOnce((r) => sent.push(r));
  await dispatchReply(async () => { throw new Error('async boom'); }, reply);
  assert.deepEqual(sent, [{ ok: false, error: 'async boom' }]);
  pass('dispatchReply: a rejected promise becomes { ok: false, error }');
}
{
  const sent = [];
  const reply = replyOnce((r) => sent.push(r));
  await dispatchReply(async () => { reply({ success: true, n: 1 }); throw new Error('after reply'); }, reply);
  assert.deepEqual(sent, [{ n: 1, ok: true }], 'an error after the reply never sends a second one');
  pass('dispatchReply: an error after replying is not a second reply');
}
