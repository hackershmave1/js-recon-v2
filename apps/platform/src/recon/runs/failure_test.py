"""Tests for run-failure classification.

These deliberately drive the REAL raise sites (``egress.validate_target``,
``fetch.fetch_url``'s HTTP path) rather than hardcoded message copies, so a
reword at the source breaks classification here instead of silently in prod
(``runs/failure.py`` is outside the mypy-strict set, so the test carries the
weight). DNS/SSRF are stubbed via ``socket.getaddrinfo`` — no network, hermetic.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from recon.domain import RunStage
from recon.fetch import egress, fetch
from recon.findings.engines import EngineError, EngineTimeout
from recon.queue import retry
from recon.runs.failure import FailureCategory, classify_failure

_SCOPE = ["acme.io"]


def _capture(fn):
    """Run ``fn`` and return the exception it raised (fails if none)."""
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - capturing the real raise to classify
        return exc
    raise AssertionError("expected an exception")


def _public_dns(monkeypatch):
    def resolver(host, service, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", service))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)


def _mock(status: int):
    return httpx.MockTransport(lambda request: httpx.Response(status))


# --- egress-layer failures (real validate_target) ---------------------------


def test_out_of_scope_extracts_host():
    exc = _capture(lambda: egress.validate_target("https://evil.example/x", _SCOPE))
    info = classify_failure(exc, RunStage.FETCHING)
    assert info.category == FailureCategory.OUT_OF_SCOPE
    assert info.host == "evil.example"
    assert "evil.example" in info.reason


def test_crawl_seed_wrap_preserves_scope_classification():
    # discover/crawl.py wraps the EgressBlocked into a FatalError string; the
    # inner scope message must still classify + yield the host.
    inner = _capture(lambda: egress.validate_target("https://evil.example/x", _SCOPE))
    wrapped = retry.FatalError(f"crawl seed blocked by egress guard: {inner}")
    info = classify_failure(wrapped)
    assert info.category == FailureCategory.OUT_OF_SCOPE
    assert info.host == "evil.example"


def test_dns_failure_classified(monkeypatch):
    def boom(host, service, *args, **kwargs):
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    exc = _capture(lambda: egress.validate_target("https://acme.io/", _SCOPE))
    info = classify_failure(exc)
    assert info.category == FailureCategory.DNS_ERROR
    assert info.host == "acme.io"


def test_private_address_blocked_never_leaks_ip(monkeypatch):
    def private(host, service, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", service))]

    monkeypatch.setattr(socket, "getaddrinfo", private)
    exc = _capture(lambda: egress.validate_target("https://acme.io/", _SCOPE))
    info = classify_failure(exc)
    assert info.category == FailureCategory.BLOCKED_ADDRESS
    # M1: the resolved internal IP must NOT reach the surfaced reason.
    assert "10.0.0.5" not in info.reason
    assert info.host is None


def test_invalid_scheme_classified():
    exc = _capture(lambda: egress.validate_target("ftp://acme.io/x", _SCOPE))
    info = classify_failure(exc)
    assert info.category == FailureCategory.INVALID_TARGET


def test_out_of_scope_suppresses_private_ip_host():
    # A redirect to an internal IP is scope-rejected BEFORE the public-IP guard, so the
    # message carries the IP — it must not be echoed (review #2). Scope reject needs no DNS.
    exc = _capture(lambda: egress.validate_target("http://10.0.0.5/", _SCOPE))
    info = classify_failure(exc)
    assert info.category == FailureCategory.OUT_OF_SCOPE
    assert info.host is None
    assert "10.0.0.5" not in info.reason


def test_no_addresses_resolved_is_dns_error(monkeypatch):
    # getaddrinfo returning [] is a resolution failure, not a non-public-address block.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    exc = _capture(lambda: egress.validate_target("https://acme.io/", _SCOPE))
    info = classify_failure(exc)
    assert info.category == FailureCategory.DNS_ERROR
    assert info.host == "acme.io"


def test_not_authorized_matches_recon_and_egress():
    # discover/capture raise "...for recon", fetch raises "...for egress" — both classify
    # (review #1: the discover-stage form was previously misclassified as UNKNOWN).
    for msg in ("session is not authorized for recon", "session is not authorized for egress"):
        info = classify_failure(egress.EgressBlocked(msg))
        assert info.category == FailureCategory.NOT_AUTHORIZED


# --- HTTP-status failures (real fetch.fetch_url + MockTransport) -------------


@pytest.mark.parametrize("status", [401, 403])
def test_access_denied_from_http(monkeypatch, status):
    _public_dns(monkeypatch)
    exc = _capture(
        lambda: fetch.fetch_url(
            "https://acme.io/app.js", _SCOPE, timeout_s=5, max_bytes=1000, transport=_mock(status)
        )
    )
    info = classify_failure(exc)
    assert info.category == FailureCategory.ACCESS_DENIED
    assert info.http_status == status
    # M3: do not assert a specific WAF vendor; point to the capture extension.
    assert "cloudflare" not in info.reason.lower()
    assert "capture extension" in info.reason.lower()


def test_rate_limited_from_http(monkeypatch):
    _public_dns(monkeypatch)
    exc = _capture(
        lambda: fetch.fetch_url(
            "https://acme.io/app.js", _SCOPE, timeout_s=5, max_bytes=1000, transport=_mock(429)
        )
    )
    info = classify_failure(exc)
    assert info.category == FailureCategory.RATE_LIMITED
    assert info.http_status == 429


def test_server_error_from_http(monkeypatch):
    _public_dns(monkeypatch)
    exc = _capture(
        lambda: fetch.fetch_url(
            "https://acme.io/app.js", _SCOPE, timeout_s=5, max_bytes=1000, transport=_mock(503)
        )
    )
    info = classify_failure(exc)
    assert info.category == FailureCategory.SERVER_ERROR
    assert info.http_status == 503


def test_other_http_error(monkeypatch):
    _public_dns(monkeypatch)
    exc = _capture(
        lambda: fetch.fetch_url(
            "https://acme.io/app.js", _SCOPE, timeout_s=5, max_bytes=1000, transport=_mock(404)
        )
    )
    info = classify_failure(exc)
    assert info.category == FailureCategory.HTTP_ERROR
    assert info.http_status == 404


# --- engine + unknown (message/stderr must never leak) -----------------------


def test_engine_error_reason_is_generic():
    exc = EngineError("kingfisher exited 2", stderr=b"AKIAIOSFODNN7EXAMPLE matched")
    info = classify_failure(exc)
    assert info.category == FailureCategory.ENGINE_ERROR
    assert "AKIA" not in info.reason
    assert "kingfisher" not in info.reason


def test_engine_timeout_classified():
    info = classify_failure(EngineTimeout("kingfisher timed out"))
    assert info.category == FailureCategory.TIMEOUT


def test_unknown_never_echoes_raw_message():
    exc = RuntimeError("boom at /etc/secrets/internal.key")
    info = classify_failure(exc, RunStage.ANALYZING)
    assert info.category == FailureCategory.UNKNOWN
    assert "/etc/secrets" not in info.reason
    assert "analyzing" in info.reason
