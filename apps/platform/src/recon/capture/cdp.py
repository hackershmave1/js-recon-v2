"""Low-level Chrome DevTools Protocol transport for the capture driver.

Slice 2 drives the WHOLE target tree — the page plus dedicated / shared workers
(C7) and service workers (C8) — over ONE websocket using flat, session-multiplexed
CDP: a single connection to the BROWSER target, ``Target.setAutoAttach{flatten:true}``
waterfalled to every child session, each command/event addressed by a ``sessionId``.

Two hard-won protocol facts drive the shapes here (verified against the CDP spec +
Puppeteer, 2026-08-11):

- Command ``id`` s are kept GLOBALLY unique by one monotonic counter, and a response
  is matched by that BARE ``id``. Chrome may answer a session-addressed command
  WITHOUT echoing ``sessionId`` (Puppeteer #14975), so matching on ``(sessionId, id)``
  would silently drop those replies — a script's source would never arrive. Only the
  outbound command needs ``sessionId`` (to address the child session); the reply is
  matched by ``id`` alone.
- ``sessionId`` attributes an EVENT (or a parsed script) to a session. Parsed-script
  metadata must be keyed by ``(sessionId, scriptId)`` because ``scriptId`` is only
  per-session unique — the same numeric id can name different scripts in the page and
  in a worker.
"""

from __future__ import annotations

import json
from typing import Any

# Auto-attach needs TWO filters, because Chromium REJECTS a filter that allows both
# `tab` and `page` at once ("Filter should not simultaneously allow \"tab\" and
# \"page\", page targets are attached via tab targets" — verified on Chromium 151).
# A page is reachable only THROUGH its `tab` parent, so:
#   - at the BROWSER root we allow `tab` (+ workers / service workers) but exclude
#     `page` — this attaches the tab and every browser-level worker/SW;
#   - waterfalled onto each attached session we allow `page` (+ workers / iframes)
#     but exclude `tab`, so the tab's page attaches and the page's workers attach.
# The default filter ([...,{type:"tab",exclude},{}]) excludes `tab`, so it would
# never reach the page at all — hence these explicit filters.
_COMMON: dict[str, Any] = {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}
ROOT_AUTO_ATTACH_PARAMS: dict[str, Any] = {
    **_COMMON,
    "filter": [{"type": "browser", "exclude": True}, {"type": "page", "exclude": True}, {}],
}
CHILD_AUTO_ATTACH_PARAMS: dict[str, Any] = {
    **_COMMON,
    "filter": [{"type": "tab", "exclude": True}, {}],
}

# Target types that have a JS VM worth Debugger.enable-ing. A `tab` (and `browser`)
# is a container with no debuggable context — enabling Debugger on it is a protocol
# error, so we only waterfall/release those, never enable them.
DEBUGGABLE_TYPES = frozenset({"page", "iframe", "worker", "service_worker", "shared_worker"})


class CaptureError(Exception):
    """The capture browser could not be launched, reached, or driven."""


class CdpSession:
    """Minimal multiplexed CDP client over one sync websocket.

    One monotonic ``id`` counter is shared across ALL sessions, so every command id
    is globally unique and a response can be matched by its bare ``id`` even when
    Chrome omits ``sessionId`` on the reply. ``send`` addresses a child session by
    passing ``session_id``; ``recv`` returns the raw decoded frame (the caller reads
    the top-level ``sessionId`` to attribute events)."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._id = 0

    def send(
        self, method: str, params: dict | None = None, *, session_id: str | None = None
    ) -> int:
        self._id += 1
        frame: dict[str, Any] = {"id": self._id, "method": method, "params": params or {}}
        if session_id is not None:
            frame["sessionId"] = session_id
        self._ws.send(json.dumps(frame))
        return self._id

    def recv(self, timeout: float) -> dict | None:
        # ConnectionClosed (browser gone) intentionally propagates to the caller; a
        # non-JSON/binary frame (CDP never sends one) is skipped, not misrouted.
        try:
            raw = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None
