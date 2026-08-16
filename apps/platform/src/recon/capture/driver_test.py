import json
import time
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
    """Scenario-driven CDP peer for the browser-level, multi-session driver.

    ``graph`` maps a session (``None`` = the browser root) to the targets its
    ``Target.setAutoAttach`` reveals — so a browser→tab→page→worker waterfall plus a
    browser→service_worker attach is expressed as data. ``scripts`` maps a session to
    the scripts it parses (each carries its own source). A paused target
    (``waitingForDebugger``) only parses AFTER ``Runtime.runIfWaitingForDebugger``
    releases it; the page parses on ``Page.navigate``. getScriptSource replies are
    emitted WITHOUT a ``sessionId`` — mirroring real Chrome — so the driver's bare-id
    matching is exercised."""

    def __init__(
        self, *, graph, scripts, nav_error=None, requests=None, responses=None, generator=None
    ):
        self._graph = graph
        self._scripts = scripts
        self._nav_error = nav_error
        # requests: session -> list of Network.requestWillBeSent `params` dicts
        self._requests = requests or {}
        # responses: session -> list of Network.responseReceived `params` dicts (Task 7)
        self._responses = responses or {}
        # generator: the <meta name=generator> content the page's Runtime.evaluate
        # "sees" (Task 7); None -> the fake replies with no value, like a page with no
        # such tag, so every OTHER test (which doesn't care about meta) stays fast and
        # unaffected.
        self._generator = generator
        self._sources = {s["scriptId"]: s["source"] for specs in scripts.values() for s in specs}
        self._out: list[dict] = []
        self.enabled: set = set()
        self.released: set = set()
        self.auto_attached: list = []

    def send(self, raw):
        msg = json.loads(raw)
        method = msg.get("method")
        sid = msg.get("sessionId")
        params = msg.get("params") or {}
        if method == "Target.setAutoAttach":
            self.auto_attached.append(sid)
            for child in self._graph.get(sid, []):
                self._out.append(
                    {
                        "method": "Target.attachedToTarget",
                        "params": {
                            "sessionId": child["sessionId"],
                            "targetInfo": {"type": child["type"], "url": child.get("url", "")},
                            "waitingForDebugger": child.get("waitingForDebugger", False),
                        },
                    }
                )
        elif method == "Debugger.enable":
            self.enabled.add(sid)
        elif method == "Runtime.runIfWaitingForDebugger":
            self.released.add(sid)
            self._emit_scripts(sid)  # a released target now runs and parses
        elif method == "Page.navigate":
            if self._nav_error:
                self._out.append({"id": msg["id"], "result": {"errorText": self._nav_error}})
            else:
                self._out.append({"id": msg["id"], "result": {"frameId": "f"}})
                self._emit_scripts(sid)  # the page (not paused) parses on navigation
        elif method == "Debugger.getScriptSource":
            src = self._sources.get(params["scriptId"])
            if src is None:
                self._out.append({"id": msg["id"], "error": {"message": "no source"}})
            else:  # NB: no sessionId on the reply — Chrome may omit it (Puppeteer #14975)
                self._out.append({"id": msg["id"], "result": {"scriptSource": src}})
        elif method == "Runtime.evaluate":
            # The driver's post-settle meta-generator read (Task 7) lands here for
            # every real capture; answer it immediately (like _InteractionWS already
            # does for its own eval calls) so every test that doesn't care about meta
            # stays fast rather than waiting out the pump's phase deadline.
            value = {"value": self._generator} if self._generator is not None else {}
            self._out.append({"id": msg["id"], "result": {"result": value}})

    def _emit_scripts(self, sid):
        for s in self._scripts.get(sid, []):
            self._out.append(
                {
                    "method": "Debugger.scriptParsed",
                    "sessionId": sid,
                    "params": {
                        "scriptId": s["scriptId"],
                        "url": s.get("url", ""),
                        "sourceMapURL": s.get("sourceMapURL", ""),
                    },
                }
            )
        for p in self._requests.get(sid, []):  # REQ-C3: XHR/fetch the context issues
            self._out.append({"method": "Network.requestWillBeSent", "sessionId": sid, "params": p})
        for p in self._responses.get(sid, []):  # Task 7: allowlisted-header harvest
            self._out.append({"method": "Network.responseReceived", "sessionId": sid, "params": p})

    def recv(self, timeout=None):
        if self._out:
            return json.dumps(self._out.pop(0))
        time.sleep(0.002)  # don't busy-spin the settle loop at full CPU in the host lane
        raise TimeoutError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(ws, on_progress=lambda _n: None, max_requests=0):
    with (
        patch("recon.capture.driver.subprocess.Popen", return_value=_FakeProc()),
        patch("recon.capture.driver._read_devtools_port", return_value=9999),
        patch("recon.capture.driver._browser_ws_url", return_value="ws://127.0.0.1:9999/browser"),
        patch("recon.capture.driver.connect", return_value=ws),
        patch("recon.capture.driver.shutil.rmtree"),
        # The real handshake needs ~1s; the fake is instant, so shrink the settle floor
        # and the wall-clock bounds to keep the host lane fast (no real browser here).
        patch("recon.capture.driver._MIN_DRIVE_SECONDS", 0.05),
    ):
        return driver.capture_scripts(
            "https://acme.io/",
            chrome_path="/usr/bin/chromium",
            nav_timeout_s=0.6,
            idle_settle_s=0.02,
            session_budget_s=1.0,
            heartbeat_interval_s=0.01,
            max_scripts=100,
            max_script_bytes=1 << 20,
            max_requests=max_requests,
            on_progress=on_progress,
        )


# The canonical tree: browser → tab → page → dedicated worker, plus a service worker
# attached at the browser level (NOT under the page — the C8 case).
_TREE = {
    None: [
        {"sessionId": "tab", "type": "tab", "waitingForDebugger": False},
        {"sessionId": "sw", "type": "service_worker", "waitingForDebugger": True},
    ],
    "tab": [{"sessionId": "page", "type": "page", "waitingForDebugger": False}],
    "page": [{"sessionId": "wk", "type": "worker", "waitingForDebugger": True}],
}
_TREE_SCRIPTS = {
    "page": [
        {
            "scriptId": "p1",
            "url": "https://acme.io/app.js",
            "sourceMapURL": "app.js.map",
            "source": "EXTERNAL",
        },
        {"scriptId": "p2", "url": "https://acme.io/", "sourceMapURL": "", "source": "INLINE"},
        {"scriptId": "p3", "url": "", "sourceMapURL": "", "source": "EVAL_C16"},
    ],
    "sw": [
        {"scriptId": "sw1", "url": "https://acme.io/sw.js", "sourceMapURL": "", "source": "SW_BODY"}
    ],
    "wk": [
        {
            "scriptId": "wk1",
            "url": "https://acme.io/worker.js",
            "sourceMapURL": "",
            "source": "WORKER_BODY",
        }
    ],
}


def test_captures_page_worker_and_service_worker_across_the_tree():
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS)
    result = _run(ws)

    by_source = {s.source.decode(): s for s in result.scripts}
    # Page (C1 external / C2 inline / C16 eval) AND the worker (C7) AND the service
    # worker (C8) are all recovered — the point of slice 2.
    assert set(by_source) == {"EXTERNAL", "INLINE", "EVAL_C16", "SW_BODY", "WORKER_BODY"}
    assert by_source["WORKER_BODY"].target_type == "worker"
    assert by_source["SW_BODY"].target_type == "service_worker"
    assert by_source["EVAL_C16"].target_type == "page"
    assert by_source["EXTERNAL"].source_map_url == "app.js.map"
    assert result.nav_error is None


def test_page_is_reached_through_its_tab_parent_no_regression():
    # Must-fix #1: the browser-level auto-attach must waterfall through the `tab`
    # target to the page; if it didn't, the page scripts (the whole slice-1 win)
    # would silently vanish. The `tab` is auto-attached but never Debugger.enable'd.
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS)
    result = _run(ws)
    page_sources = {s.source.decode() for s in result.scripts if s.target_type == "page"}
    assert page_sources == {"EXTERNAL", "INLINE", "EVAL_C16"}
    assert "tab" in ws.auto_attached  # waterfalled onto the tab
    assert "tab" not in ws.enabled  # ...but a tab has no debuggable VM


def test_paused_worker_and_service_worker_are_released_deadlock_guard():
    # Every waitingForDebugger target must get Runtime.runIfWaitingForDebugger or it
    # hangs forever (the SW paused-start deadlock). The page was not paused.
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS)
    _run(ws)
    assert ws.released == {"sw", "wk"}
    assert {"page", "sw", "wk"} <= ws.enabled  # Debugger.enable on every debuggable session


def test_getscriptsource_reply_without_sessionid_is_matched():
    # Must-fix #2: the fake omits sessionId on every getScriptSource reply (as Chrome
    # can). A single captured script proves the driver matches by bare id, not
    # (sessionId, id) — which would drop the reply and the source.
    graph = {None: [{"sessionId": "page", "type": "page", "waitingForDebugger": False}]}
    scripts = {"page": [{"scriptId": "p1", "url": "https://acme.io/a.js", "source": "ONLY"}]}
    result = _run(_FakeWS(graph=graph, scripts=scripts))
    assert [s.source.decode() for s in result.scripts] == ["ONLY"]


def test_dedupes_identical_source_by_hash():
    graph = {None: [{"sessionId": "page", "type": "page", "waitingForDebugger": False}]}
    scripts = {
        "page": [
            {"scriptId": "p1", "url": "https://acme.io/a.js", "source": "IDENTICAL"},
            {"scriptId": "p2", "url": "https://acme.io/b.js", "source": "IDENTICAL"},
        ]
    }
    result = _run(_FakeWS(graph=graph, scripts=scripts))
    assert len(result.scripts) == 1  # byte-identical scripts collapse to one


def test_navigation_error_is_reported():
    # A hard Page.navigate failure (errorText) is surfaced so the stage records the
    # run "blocked" rather than a false "ok" with zero scripts.
    graph = {
        None: [{"sessionId": "tab", "type": "tab", "waitingForDebugger": False}],
        "tab": [{"sessionId": "page", "type": "page", "waitingForDebugger": False}],
    }
    result = _run(_FakeWS(graph=graph, scripts={}, nav_error="net::ERR_CONNECTION_REFUSED"))
    assert result.nav_error == "net::ERR_CONNECTION_REFUSED"
    assert result.scripts == []


def test_no_page_target_is_blocked_not_a_false_ok():
    # If the tree never yields a page (e.g. a target-topology regression), the run is
    # reported blocked, never a silent "ok" with zero scripts.
    graph = {None: [{"sessionId": "sw", "type": "service_worker", "waitingForDebugger": True}]}
    scripts = {"sw": [{"scriptId": "sw1", "url": "", "source": "SW_ONLY"}]}
    result = _run(_FakeWS(graph=graph, scripts=scripts))
    assert result.nav_error == "capture never attached to a page target"


def test_on_progress_raise_aborts_and_propagates():
    # The stage's pause/cancel check is wired through on_progress; a raise must abort
    # the drive (Chromium is killed in finally).
    calls = {"n": 0}

    def boom(_n):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        _run(_FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS), on_progress=boom)


# REQ-C3: the page issues XHR/fetch requests whose URLs resolve the runtime host of the
# statically-extracted endpoints. A `Script` load and a `data:` URL must be ignored; a
# duplicate (same method + path) and the query string must be dropped.
_TREE_REQUESTS = {
    "page": [
        {
            "type": "XHR",
            "request": {"url": "https://api.acme.io/get-job-types?token=SECRET", "method": "GET"},
        },
        {
            "type": "XHR",
            "request": {"url": "https://api.acme.io/get-job-types?token=OTHER", "method": "GET"},
        },
        {"type": "Fetch", "request": {"url": "https://api.acme.io/getJobId", "method": "POST"}},
        {"type": "Script", "request": {"url": "https://api.acme.io/vendor.js", "method": "GET"}},
        {"type": "XHR", "request": {"url": "data:application/json,{}", "method": "GET"}},
    ]
}


def test_records_observed_xhr_fetch_requests_deduped_and_path_only():
    # Only XHR/Fetch http(s) requests are recorded, deduped on (method, path-only url)
    # with the query string dropped (no token custody) — while scripts capture unchanged.
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS, requests=_TREE_REQUESTS)
    result = _run(ws, max_requests=100)
    assert {s.source.decode() for s in result.scripts} == {
        "EXTERNAL",
        "INLINE",
        "EVAL_C16",
        "SW_BODY",
        "WORKER_BODY",
    }
    assert result.requests == [
        {"method": "GET", "url": "https://api.acme.io/get-job-types"},
        {"method": "POST", "url": "https://api.acme.io/getJobId"},
    ]


def test_request_recording_is_off_when_max_requests_zero():
    # Default max_requests=0 records nothing — non-capture callers pay no cost and the
    # field stays empty.
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS, requests=_TREE_REQUESTS)
    assert _run(ws).requests == []


def test_malformed_request_frame_never_aborts_capture():
    # A frame missing `request`/`type` must be skipped, not raise — one bad event must
    # never abort the capture (which would lose every script).
    reqs = {
        "page": [
            {"type": "XHR"},  # no request
            {},  # no type
            {"type": "XHR", "request": None},  # null request
            {"type": "XHR", "request": {"url": "https://api.acme.io/ok", "method": "GET"}},
        ]
    }
    result = _run(_FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS, requests=reqs), max_requests=100)
    assert result.requests == [{"method": "GET", "url": "https://api.acme.io/ok"}]
    assert len(result.scripts) == 5  # every tree script survived the bad frames


def test_request_cap_is_enforced():
    reqs = {
        "page": [
            {"type": "XHR", "request": {"url": f"https://api.acme.io/e{i}", "method": "GET"}}
            for i in range(10)
        ]
    }
    result = _run(_FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS, requests=reqs), max_requests=3)
    assert len(result.requests) == 3


# Task 7: end-to-end (no real browser, still the fake-websocket host lane) proof that
# capture_scripts() plumbs Network.responseReceived frames and the post-settle
# <meta name=generator> eval all the way through to CaptureResult. The pure
# _Ctx-level tests in capture_signal_test.py cover the harvest logic in isolation;
# this proves the wiring (_route dispatch -> _on_response -> _drive's return) is
# actually connected for a full capture_scripts() call.
_TREE_RESPONSES = {
    "page": [
        {
            "response": {
                "url": "https://acme.io/",
                "headers": {
                    "Server": "nginx/1.25.3",
                    "Authorization": "Bearer should-never-appear",
                    "Set-Cookie": "sid=SECRETVALUE; Path=/\ntheme=dark",
                },
            }
        },
        {
            "response": {
                "url": "https://evil.cdn/track.js",  # a different host -> its own entry
                "headers": {"Server": "Apache"},
            }
        },
    ]
}


def test_response_headers_and_cookies_are_harvested_per_host():
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS, responses=_TREE_RESPONSES)
    result = _run(ws)
    assert result.headers_by_host["acme.io"] == {"server": "nginx/1.25.3"}
    assert result.cookies_by_host["acme.io"] == ["sid", "theme"]
    assert "authorization" not in result.headers_by_host["acme.io"]  # never allowlisted (T1)
    assert result.headers_by_host["evil.cdn"] == {"server": "Apache"}


def test_meta_generator_is_read_once_after_settle():
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS, generator="WordPress 6.4")
    result = _run(ws)
    assert result.meta == ["WordPress 6.4"]


def test_no_meta_tag_leaves_meta_empty():
    ws = _FakeWS(graph=_TREE, scripts=_TREE_SCRIPTS)  # generator=None (default)
    result = _run(ws)
    assert result.meta == []


def test_blocked_run_never_waits_on_the_meta_eval():
    # No page ever attaches -> nav_error is set before the meta read -> evaluate()
    # must short-circuit instantly rather than waiting out its 2s timeout budget.
    graph = {None: [{"sessionId": "sw", "type": "service_worker", "waitingForDebugger": True}]}
    scripts = {"sw": [{"scriptId": "sw1", "url": "", "source": "SW_ONLY"}]}
    result = _run(_FakeWS(graph=graph, scripts=scripts))
    assert result.nav_error == "capture never attached to a page target"
    assert result.meta == []


class _InteractionWS:
    """Interaction scenario: browser -> tab -> page; the page parses SEED on the initial
    navigation. Route-enum finds one route; navigating it DESTROYS the seed page's
    context (a late ``getScriptSource`` for SEED then errors) and parses ROUTE. Autoscroll
    and click-all are no-ops here (no lazy content, no clickables) so the test isolates
    route-enum + eager fetch. Both SEED and ROUTE survive ONLY because each source is
    fetched the instant it parses — a collect-then-fetch pass would lose SEED."""

    def __init__(self):
        self._out: list[dict] = []
        self._live = {"seed": "SEED", "route": "ROUTE"}  # scriptId -> currently-fetchable source
        self._navigated_once = False
        self.evals: list[str] = []

    def _emit(self, frame):
        self._out.append(frame)

    def _attach(self, sid, ttype):
        self._emit(
            {
                "method": "Target.attachedToTarget",
                "params": {
                    "sessionId": sid,
                    "targetInfo": {"type": ttype, "url": ""},
                    "waitingForDebugger": False,
                },
            }
        )

    def _parse(self, script_id, url):
        self._emit(
            {
                "method": "Debugger.scriptParsed",
                "sessionId": "page",
                "params": {"scriptId": script_id, "url": url, "sourceMapURL": ""},
            }
        )

    def send(self, raw):
        msg = json.loads(raw)
        method = msg.get("method")
        sid = msg.get("sessionId")
        params = msg.get("params") or {}
        mid = msg.get("id")
        if method == "Target.setAutoAttach":
            if sid is None:
                self._attach("tab", "tab")
            elif sid == "tab":
                self._attach("page", "page")
        elif method == "Page.navigate":
            self._emit({"id": mid, "result": {"frameId": "f"}})
            if not self._navigated_once:
                self._navigated_once = True
                self._parse("seed", "https://acme.io/app.js")
            else:  # route navigation destroys the seed context, then parses the route
                self._live.pop("seed", None)
                self._parse("route", "https://acme.io/r1.js")
        elif method == "Runtime.evaluate":
            expr = params.get("expression", "")
            self.evals.append(expr)
            if "u.hash" in expr:  # _ROUTES_JS
                value = {"value": ["https://acme.io/r1"]}
            elif "setAttribute('data-recon-idx'" in expr:  # _SNAPSHOT_JS
                value = {"value": 0}
            else:  # autoscroll / clicks: no value -> caller stops
                value = {}
            self._emit({"id": mid, "result": {"result": value}})
        elif method == "Debugger.getScriptSource":
            src = self._live.get(params["scriptId"])
            if src is None:
                self._emit({"id": mid, "error": {"message": "no script for id"}})
            else:
                self._emit({"id": mid, "result": {"scriptSource": src}})
        # Debugger.enable / Page.enable / Runtime.runIfWaitingForDebugger: no reply needed

    def recv(self, timeout=None):
        if self._out:
            return json.dumps(self._out.pop(0))
        time.sleep(0.002)
        raise TimeoutError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run_interactive(ws):
    with (
        patch("recon.capture.driver.subprocess.Popen", return_value=_FakeProc()),
        patch("recon.capture.driver._read_devtools_port", return_value=9999),
        patch("recon.capture.driver._browser_ws_url", return_value="ws://127.0.0.1:9999/browser"),
        patch("recon.capture.driver.connect", return_value=ws),
        patch("recon.capture.driver.shutil.rmtree"),
        patch("recon.capture.driver._MIN_DRIVE_SECONDS", 0.05),
    ):
        return driver.capture_scripts(
            "https://acme.io/",
            chrome_path="/usr/bin/chromium",
            nav_timeout_s=0.6,
            idle_settle_s=0.02,
            session_budget_s=3.0,
            heartbeat_interval_s=0.01,
            max_scripts=100,
            max_script_bytes=1 << 20,
            interact=True,
            max_scroll_steps=3,
            max_clicks=5,
            max_routes=5,
        )


def test_interaction_route_enum_captures_each_route_with_eager_fetch():
    # Route-enum navigates the page a second time, destroying the seed context. Both
    # SEED (fetched on parse, before the navigation) and ROUTE are captured — the
    # eager fetch-on-parse guarantee (must-fix: a deferred final fetch would lose SEED).
    ws = _InteractionWS()
    result = _run_interactive(ws)
    assert {s.source.decode() for s in result.scripts} == {"SEED", "ROUTE"}
    assert result.nav_error is None
    assert ws._navigated_once  # the route navigation actually happened
