"""Tests for the capture-stage fingerprint-signal harvest (Task 7).

Pure — no live browser, no real websocket. ``_Ctx._route``/``_on_response`` are
driven directly with synthetic ``Network.responseReceived``-shaped dicts (the CDP
event shape: ``response.headers`` is a flat name->value dict, and a repeated header
like Set-Cookie is folded into ONE value joined by ``"\\n"`` — the same shape
``_on_request``'s sibling ``Network.requestWillBeSent`` handling already assumes
for its own params). ``stage._build_signal`` is driven with a plain
``CaptureResult``. Neither needs Chromium or a real CDP connection.

See ``driver_test.py`` for the end-to-end (still no-live-browser, fake-websocket)
proof that ``capture_scripts()`` wires ``Network.responseReceived`` frames and the
``<meta generator>`` eval through to ``CaptureResult`` — a full CDP integration
test against a REAL Chromium could not be run in this environment (chromium
launch fails here; the repo's pre-existing capture integration tests share that
limitation), so the fake-websocket path is this feature's thorough substitute.
"""

from __future__ import annotations

import time

from recon.capture import stage
from recon.capture.cdp import CdpSession
from recon.capture.driver import CapturedScript, CaptureResult, _Beater, _Ctx

# ---- pure _Ctx / _on_response tests (driver-side harvest) ----


def _ctx() -> _Ctx:
    """A minimally-constructed ``_Ctx`` for driving ``_route``/``_on_response``
    directly. That path never touches ``state`` (only the send/recv-driven
    branches like ``_on_attached``/``evaluate`` do), so a real-but-unconnected
    ``CdpSession`` is a safe, honestly-typed stand-in — no websocket required."""
    return _Ctx(
        CdpSession(None),
        target_url="https://acme.io/",
        deadline=time.perf_counter() + 10.0,
        idle_settle_s=0.1,
        min_drive_s=0.0,
        max_scripts=100,
        max_script_bytes=1 << 20,
        max_requests=0,
        beater=_Beater(lambda _n: None, 999.0),
    )


def _response_frame(url: str, headers: dict[str, str]) -> dict:
    return {
        "method": "Network.responseReceived",
        "params": {"response": {"url": url, "headers": headers}},
    }


def test_route_dispatches_response_frames_to_on_response():
    ctx = _ctx()
    ctx._route(_response_frame("https://acme.io/", {"Server": "nginx/1.25.3"}))
    assert ctx.headers_by_host["acme.io"] == {"server": "nginx/1.25.3"}


def test_allowlisted_header_kept_others_dropped():
    ctx = _ctx()
    ctx._on_response(
        _response_frame(
            "https://acme.io/app.js",
            {"Server": "nginx", "Authorization": "Bearer super-secret", "X-Random": "noise"},
        )
    )
    assert ctx.headers_by_host["acme.io"] == {"server": "nginx"}


def test_set_cookie_yields_names_only_never_values():
    ctx = _ctx()
    ctx._on_response(
        _response_frame(
            "https://acme.io/",
            {"Set-Cookie": "sid=SECRETVALUE; Path=/; HttpOnly\ntheme=dark"},
        )
    )
    assert ctx.cookies_by_host["acme.io"] == ["sid", "theme"]


def test_repeated_responses_accumulate_without_losing_earlier_headers():
    # A second response on the SAME host must fold in, not replace, the first's
    # allowlisted headers — the point of setdefault(...).update(...) (T6-adjacent:
    # one signal per host, not "latest response wins").
    ctx = _ctx()
    ctx._on_response(_response_frame("https://acme.io/", {"Server": "nginx"}))
    ctx._on_response(_response_frame("https://acme.io/app.js", {"X-Powered-By": "Express"}))
    assert ctx.headers_by_host["acme.io"] == {"server": "nginx", "x-powered-by": "Express"}


def test_different_hosts_get_independent_entries():
    ctx = _ctx()
    ctx._on_response(_response_frame("https://acme.io/", {"Server": "nginx"}))
    ctx._on_response(_response_frame("https://cdn.acme.io/lib.js", {"Server": "Apache"}))
    assert ctx.headers_by_host == {
        "acme.io": {"server": "nginx"},
        "cdn.acme.io": {"server": "Apache"},
    }


def test_malformed_response_frame_never_raises():
    # TOTAL by construction (mirrors _on_request's docstring) — a bad frame is
    # skipped, never raised, so one malformed event can't abort the whole capture.
    ctx = _ctx()
    ctx._on_response({})  # no params at all
    ctx._on_response({"params": {}})  # no response
    ctx._on_response({"params": {"response": {}}})  # no url
    ctx._on_response({"params": {"response": {"url": "not-a-url", "headers": {}}}})  # no host
    assert ctx.headers_by_host == {}
    assert ctx.cookies_by_host == {}


# ---- pure stage._build_signal tests (the consolidation) ----


def test_stage_builds_host_keyed_signal_from_capture_result():
    result = CaptureResult(
        scripts=[CapturedScript("https://acme.io/app.js", b"x", None, "abc", "page")],
        nav_error=None,
        requests=[],
        headers_by_host={"acme.io": {"server": "nginx/1.25.3"}},
        cookies_by_host={"acme.io": ["sid"]},
        meta=["WordPress 6.4"],
    )
    signal = stage._build_signal(
        result,
        target_host="acme.io",
        kept=result.scripts,
    )
    assert signal["acme.io"]["headers"] == {"server": "nginx/1.25.3"}
    assert signal["acme.io"]["scripts"] == ["https://acme.io/app.js"]
    assert signal["acme.io"]["meta"] == ["WordPress 6.4"]
    assert signal["acme.io"]["cookies"] == ["sid"]


def test_signal_is_empty_when_nothing_harvested():
    result = CaptureResult(scripts=[], nav_error=None, requests=[])
    assert stage._build_signal(result, target_host="acme.io", kept=[]) == {}


def test_signal_attributes_third_party_script_to_its_own_host():
    # A kept in-scope third-party host (e.g. a subdomain the crawl's scope allowed)
    # gets its OWN entry; the meta generator only ever attaches to the document
    # (target) host, never a script's host.
    result = CaptureResult(scripts=[], nav_error=None, requests=[], meta=["Shopify"])
    third_party = CapturedScript("https://cdn.acme.io/lib.js", b"x", None, "sha", "page")
    signal = stage._build_signal(result, target_host="acme.io", kept=[third_party])
    assert signal["cdn.acme.io"]["scripts"] == ["https://cdn.acme.io/lib.js"]
    assert signal["cdn.acme.io"]["meta"] == []
    assert signal["acme.io"]["meta"] == ["Shopify"]


def test_anonymous_script_contributes_no_url_but_keeps_the_host_entry_alive():
    anon = CapturedScript("", b"x", None, "sha", "page")
    result = CaptureResult(
        scripts=[], nav_error=None, requests=[], headers_by_host={"acme.io": {"server": "nginx"}}
    )
    signal = stage._build_signal(result, target_host="acme.io", kept=[anon])
    assert signal["acme.io"]["headers"] == {"server": "nginx"}
    assert signal["acme.io"]["scripts"] == []  # no URL to record


def test_anonymous_only_script_is_filtered_out_when_nothing_else_harvested():
    anon = CapturedScript("", b"x", None, "sha", "page")
    result = CaptureResult(scripts=[], nav_error=None, requests=[])
    assert stage._build_signal(result, target_host="acme.io", kept=[anon]) == {}
