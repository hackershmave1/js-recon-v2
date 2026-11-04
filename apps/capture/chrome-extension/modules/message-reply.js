// message-reply.js — the ONE popup<->worker reply shape (C7): `{ ok, error?, ...detail }`.
//
// Worker handlers grew two conventions (`{ success }` from most, `{ ok }` from health/LLM calls),
// and the popup had to know which action spoke which. Handlers also return failures as VALUES
// (a login 401 is `{ success: false, status: 401 }`, not a throw), so the envelope must read the
// handler's own verdict, never assume success. Internal modules keep `.success`; only the wire to
// the popup is normalized, here, in one place.

// `ok` is computed from the handler's verdict and written LAST, so no handler field can override
// a failure. Detail fields (status, reason, pending, uploader, ...) ride along unchanged.
export function toWire(result) {
  const body = result && typeof result === 'object' ? { ...result } : {};
  const failed = body.ok === false || body.success === false;
  delete body.success;
  delete body.ok;
  return { ...body, ok: !failed };
}

// One reply per message, always in wire shape: Chrome ignores a second sendResponse, but a late
// failure reply after a success would otherwise be a silent lie in our own logs/tests.
export function replyOnce(sendResponse) {
  let replied = false;
  return (result) => {
    if (replied) return;
    replied = true;
    sendResponse(toWire(result));
  };
}

// Run a handler so that a thrown error or rejected promise still answers the popup — instead of
// leaving it waiting on a reply that never comes (the popup's safety net would then guess).
export async function dispatchReply(handler, reply) {
  try {
    await handler();
  } catch (error) {
    reply({ ok: false, error: error?.message || 'unknown' });
  }
}
