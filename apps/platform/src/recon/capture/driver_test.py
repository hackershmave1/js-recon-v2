import json
from unittest.mock import patch

import pytest

from recon.capture import driver


class _FakeProc:
    """Popen stand-in that reports already-exited so _kill_group is a no-op
    (host lane has no os.killpg)."""

    pid = 4321

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class _FakeWS:
    """Canned CDP peer: streams the given scriptParsed events, then answers each
    getScriptSource with the matching source (by scriptId)."""

    def __init__(self, parsed, sources, nav_error=None):
        self._events = [dict(p) for p in parsed]
        self._sources = sources
        self._nav_error = nav_error
        self._responses: list[dict] = []

    def send(self, raw):
        msg = json.loads(raw)
        if msg.get("method") == "Debugger.getScriptSource":
            sid = msg["params"]["scriptId"]
            src = self._sources.get(sid)
            if src is None:
                self._responses.append({"id": msg["id"], "error": {"message": "no source"}})
            else:
                self._responses.append({"id": msg["id"], "result": {"scriptSource": src}})
        elif msg.get("method") == "Page.navigate" and self._nav_error:
            self._responses.append({"id": msg["id"], "result": {"errorText": self._nav_error}})

    def recv(self, timeout=None):
        if self._responses:
            return json.dumps(self._responses.pop(0))
        if self._events:
            return json.dumps({"method": "Debugger.scriptParsed", "params": self._events.pop(0)})
        raise TimeoutError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(parsed, sources, on_progress=lambda _n: None, nav_error=None):
    ws = _FakeWS(parsed, sources, nav_error=nav_error)
    with (
        patch("recon.capture.driver.subprocess.Popen", return_value=_FakeProc()),
        patch("recon.capture.driver._read_devtools_port", return_value=9999),
        patch("recon.capture.driver._page_ws_url", return_value="ws://127.0.0.1:9999/page"),
        patch("recon.capture.driver.connect", return_value=ws),
        patch("recon.capture.driver.shutil.rmtree"),
    ):
        return driver.capture_scripts(
            "https://acme.io/",
            chrome_path="/usr/bin/chromium",
            nav_timeout_s=5.0,
            idle_settle_s=0.05,
            session_budget_s=5.0,
            heartbeat_interval_s=0.01,
            max_scripts=100,
            max_script_bytes=1 << 20,
            on_progress=on_progress,
        )


def test_captures_external_inline_and_anonymous_scripts():
    parsed = [
        {"scriptId": "1", "url": "https://acme.io/app.js", "sourceMapURL": "app.js.map"},
        {"scriptId": "2", "url": "https://acme.io/", "sourceMapURL": ""},  # inline: doc URL
        {"scriptId": "3", "url": "", "sourceMapURL": ""},  # anonymous eval'd — the C16 case
    ]
    sources = {
        "1": "console.log('external')",
        "2": "window.__INLINE__=1",
        "3": "eval-generated-marker",
    }
    result = _run(parsed, sources)
    by_source = {s.source.decode(): s for s in result.scripts}
    assert set(by_source) == {
        "console.log('external')",
        "window.__INLINE__=1",
        "eval-generated-marker",
    }
    # The anonymous (VM-only) script is captured with an empty URL — the stage
    # gives it a content-addressed synthetic URL.
    anon = by_source["eval-generated-marker"]
    assert anon.url == ""
    assert by_source["console.log('external')"].source_map_url == "app.js.map"
    assert all(len(s.sha256) == 64 for s in result.scripts)
    assert result.nav_error is None


def test_dedupes_identical_source_by_hash():
    parsed = [
        {"scriptId": "1", "url": "https://acme.io/a.js", "sourceMapURL": ""},
        {"scriptId": "2", "url": "https://acme.io/b.js", "sourceMapURL": ""},
    ]
    sources = {"1": "IDENTICAL", "2": "IDENTICAL"}
    result = _run(parsed, sources)
    assert len(result.scripts) == 1  # byte-identical scripts collapse to one


def test_navigation_error_is_reported():
    # A hard Page.navigate failure (errorText) is surfaced so the stage can record
    # the run "blocked" rather than a false "ok" with zero scripts.
    result = _run([], {}, nav_error="net::ERR_CONNECTION_REFUSED")
    assert result.nav_error == "net::ERR_CONNECTION_REFUSED"
    assert result.scripts == []


def test_on_progress_raise_aborts_and_propagates():
    # The stage's pause/cancel check is wired through on_progress; a raise must
    # abort the drive (Chromium is killed in finally).
    calls = {"n": 0}

    def boom(_n):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        _run(
            [{"scriptId": "1", "url": "https://acme.io/a.js", "sourceMapURL": ""}],
            {"1": "x"},
            on_progress=boom,
        )
