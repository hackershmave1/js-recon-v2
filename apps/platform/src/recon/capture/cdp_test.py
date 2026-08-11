import json

from recon.capture.cdp import (
    CHILD_AUTO_ATTACH_PARAMS,
    DEBUGGABLE_TYPES,
    ROOT_AUTO_ATTACH_PARAMS,
    CdpSession,
)


class _RecordingWS:
    def __init__(self, frames=()):
        self.sent: list = []
        self._frames = list(frames)

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self, timeout=None):
        if self._frames:
            return self._frames.pop(0)
        raise TimeoutError


def test_send_uses_one_global_monotonic_id_across_sessions():
    # Must-fix #2 core: ids are globally unique regardless of session, so a reply can
    # be matched by bare id even when Chrome omits sessionId. A per-session counter
    # would reuse ids across sessions and mis-route replies.
    ws = _RecordingWS()
    state = CdpSession(ws)
    id_a = state.send("Debugger.getScriptSource", {"scriptId": "1"}, session_id="a")
    id_b = state.send("Debugger.getScriptSource", {"scriptId": "1"}, session_id="b")
    id_root = state.send("Target.setAutoAttach", ROOT_AUTO_ATTACH_PARAMS)
    assert [id_a, id_b, id_root] == [1, 2, 3]
    assert ws.sent[0]["sessionId"] == "a"
    assert ws.sent[1]["sessionId"] == "b"
    assert "sessionId" not in ws.sent[2]  # a browser-level command carries no sessionId


def test_recv_decodes_json_and_swallows_non_json_and_timeout():
    ws = _RecordingWS(frames=[json.dumps({"id": 7, "result": {}}), b"\x00binary"])
    state = CdpSession(ws)
    assert state.recv(0.1) == {"id": 7, "result": {}}
    assert state.recv(0.1) is None  # binary/garbage frame skipped, not raised
    assert state.recv(0.1) is None  # empty -> TimeoutError -> None


def _excluded(params):
    return {f.get("type") for f in params["filter"] if f.get("exclude")}


def _allows(params, target_type):
    # Ordered-matcher semantics: the first entry whose type matches decides; a bare
    # {} matches anything. Excluded types are dropped, everything else is allowed.
    for entry in params["filter"]:
        if "type" not in entry or entry["type"] == target_type:
            return not entry.get("exclude", False)
    return False


def test_auto_attach_filters_never_allow_tab_and_page_together():
    # Chromium rejects a filter that allows both `tab` and `page` (page targets attach
    # via tab targets). The ROOT filter reaches the tab (not the page); the CHILD
    # filter, waterfalled onto the tab, reaches the page (not the tab).
    for params in (ROOT_AUTO_ATTACH_PARAMS, CHILD_AUTO_ATTACH_PARAMS):
        assert params["flatten"] is True
        assert params["waitForDebuggerOnStart"] is True
        assert not (_allows(params, "tab") and _allows(params, "page"))
    assert _allows(ROOT_AUTO_ATTACH_PARAMS, "tab")
    assert not _allows(ROOT_AUTO_ATTACH_PARAMS, "page")
    assert "browser" in _excluded(ROOT_AUTO_ATTACH_PARAMS)
    assert _allows(CHILD_AUTO_ATTACH_PARAMS, "page")
    assert _allows(CHILD_AUTO_ATTACH_PARAMS, "service_worker")
    assert not _allows(CHILD_AUTO_ATTACH_PARAMS, "tab")
    assert "service_worker" in DEBUGGABLE_TYPES
