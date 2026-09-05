"""D45b1/b2 — runtime observation ingest on analyze/start. The server-side normalizer turns
client-supplied ``{method, url}`` observations into the correlate matcher's shape (deduped,
http(s) only, query dropped, capped), preserves optional D45b2 ``reqBody``/``respBody`` text
(re-capped server-side), and the AnalyzeStartIn body stays additive/optional so an older or
non-extension client still starts analysis. Hermetic — no DB/Redis/S3."""

from __future__ import annotations

import pytest

from recon.api import capture_router
from recon.api.capture_router import AnalyzeStartIn, _capped_body, _normalize_observations


def test_normalize_observations_dedups_normalizes_and_drops_junk() -> None:
    raw = [
        {"method": "get", "url": "https://api.acme.io/v1/users?page=2"},  # lowercased + query
        {"method": "GET", "url": "https://api.acme.io/v1/users"},  # dup of #1 after normalize
        {"method": "POST", "url": "https://api.acme.io/v1/users"},  # kept (different method)
        {"method": "GET", "url": "ws://api.acme.io/socket"},  # non-http → dropped
        {"method": "", "url": "https://x.io/y"},  # no method → dropped
        {"method": "GET", "url": ""},  # no url → dropped
        "not-a-dict",  # → dropped
        {"method": "GET", "url": "https://api.acme.io:8443/svc"},  # keeps a non-default port
    ]
    assert _normalize_observations(raw) == [
        {"method": "GET", "url": "https://api.acme.io/v1/users"},
        {"method": "POST", "url": "https://api.acme.io/v1/users"},
        {"method": "GET", "url": "https://api.acme.io:8443/svc"},
    ]


def test_normalize_observations_caps_to_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Settings:
        capture_max_requests = 2
        capture_max_request_body_bytes = 64 * 1024
        capture_max_response_body_bytes = 128 * 1024

    monkeypatch.setattr(capture_router, "get_settings", lambda: _Settings())
    raw = [{"method": "GET", "url": f"https://a.io/{i}"} for i in range(5)]
    assert len(_normalize_observations(raw)) == 2


def test_analyze_start_body_is_optional_and_additive() -> None:
    # An older/non-extension client that omits observations (or the whole field) still parses.
    assert AnalyzeStartIn().observations == []
    assert AnalyzeStartIn(options={"x": 1}).observations == []
    parsed = AnalyzeStartIn(observations=[{"method": "GET", "url": "https://a/b"}])
    assert parsed.observations == [{"method": "GET", "url": "https://a/b"}]


# --------------------------------------------------------------------------- #
# D45b2 — optional per-observation body preservation (secret-scan/param-hint fuel).
# --------------------------------------------------------------------------- #


def test_capped_body_passes_small_str_and_drops_non_str() -> None:
    assert _capped_body("a=1", 64) == "a=1"
    assert _capped_body("", 64) is None  # empty
    assert _capped_body(None, 64) is None
    assert _capped_body(123, 64) is None  # non-str never trusted


def test_capped_body_truncates_oversized_without_splitting_a_codepoint() -> None:
    # Re-cap SERVER-SIDE — never trust the client's own cap. A multibyte char straddling the cap
    # is dropped whole (decode-ignore), so truncation never raises or emits a lone surrogate.
    body = "A" * 10 + "é" * 10  # 10 ASCII + 10 two-byte chars = 30 UTF-8 bytes
    capped = _capped_body(body, 11)  # cap mid-way through the first 2-byte char
    assert capped == "A" * 10  # the split é is dropped, not mangled
    assert len(capped.encode("utf-8")) <= 11


def test_normalize_observations_preserves_and_recaps_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Settings:
        capture_max_requests = 100
        capture_max_request_body_bytes = 8
        capture_max_response_body_bytes = 4

    monkeypatch.setattr(capture_router, "get_settings", lambda: _Settings())
    raw = [
        {
            "method": "POST",
            "url": "https://api.acme.io/login?next=/x",  # query dropped on url, bodies kept
            "reqBody": "u=alice&pw=secret",  # 17 bytes -> capped to 8
            "respBody": "toolong",  # 7 bytes -> capped to 4
        },
        {"method": "GET", "url": "https://api.acme.io/ping"},  # no bodies -> no body keys
    ]
    out = _normalize_observations(raw)
    assert out[0] == {
        "method": "POST",
        "url": "https://api.acme.io/login",
        "reqBody": "u=alice&",  # first 8 bytes
        "respBody": "tool",  # first 4 bytes
    }
    # A body-less observation carries no body keys (stays additive/minimal).
    assert out[1] == {"method": "GET", "url": "https://api.acme.io/ping"}


def test_normalize_observations_dedup_keeps_first_bodies() -> None:
    # Bodies do not enter the (method,url) dedup key: the first sighting wins and keeps its body.
    raw = [
        {"method": "POST", "url": "https://a.io/x", "reqBody": "first"},
        {"method": "POST", "url": "https://a.io/x", "reqBody": "second"},  # dup -> dropped
    ]
    assert _normalize_observations(raw) == [
        {"method": "POST", "url": "https://a.io/x", "reqBody": "first"}
    ]
