"""Tests for the fetch stage.

``fetch_url`` is exercised with httpx's MockTransport (no network) and a stubbed
resolver, so the scope/SSRF/redirect/cap logic is covered without a live server.
``fetch_run`` gets one integration test (DB + object storage) with ``fetch_url``
stubbed.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from recon.fetch import egress, fetch
from recon.queue import retry

_SCOPE = ["acme.io"]


def _stub_public_dns(monkeypatch):
    def resolver(host, service, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", service))]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)


def _mock(handler):
    return httpx.MockTransport(handler)


def test_fetch_returns_body(monkeypatch):
    _stub_public_dns(monkeypatch)

    def handler(request):
        assert request.url.host == "acme.io"
        return httpx.Response(200, content=b"console.log(1);")

    body = fetch.fetch_url(
        "https://acme.io/app.js", _SCOPE, timeout_s=5, max_bytes=1000, transport=_mock(handler)
    )
    assert body == b"console.log(1);"


def test_fetch_out_of_scope_blocked(monkeypatch):
    _stub_public_dns(monkeypatch)
    with pytest.raises(egress.EgressBlocked, match="scope"):
        fetch.fetch_url(
            "https://evil.example/app.js", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(200)),
        )


def test_fetch_size_cap_enforced(monkeypatch):
    _stub_public_dns(monkeypatch)
    with pytest.raises(retry.FatalError, match="exceeds"):
        fetch.fetch_url(
            "https://acme.io/big.js", _SCOPE, timeout_s=5, max_bytes=10,
            transport=_mock(lambda r: httpx.Response(200, content=b"x" * 100)),
        )


def test_fetch_follows_redirect_to_in_scope(monkeypatch):
    _stub_public_dns(monkeypatch)

    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://acme.io/b"})
        return httpx.Response(200, content=b"final")

    body = fetch.fetch_url(
        "https://acme.io/a", _SCOPE, timeout_s=5, max_bytes=1000, transport=_mock(handler)
    )
    assert body == b"final"


def test_fetch_redirect_out_of_scope_blocked(monkeypatch):
    _stub_public_dns(monkeypatch)
    with pytest.raises(egress.EgressBlocked, match="scope"):
        fetch.fetch_url(
            "https://acme.io/a", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"})),
        )


def test_fetch_redirect_bad_scheme_blocked(monkeypatch):
    _stub_public_dns(monkeypatch)
    with pytest.raises(egress.EgressBlocked, match="scheme"):
        fetch.fetch_url(
            "https://acme.io/a", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(302, headers={"location": "file:///etc/passwd"})),
        )


def test_fetch_too_many_redirects(monkeypatch):
    _stub_public_dns(monkeypatch)
    with pytest.raises(retry.FatalError, match="redirects"):
        fetch.fetch_url(
            "https://acme.io/loop", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(302, headers={"location": "https://acme.io/loop"})),
        )


def test_fetch_5xx_is_retryable(monkeypatch):
    _stub_public_dns(monkeypatch)
    with pytest.raises(retry.RetryableError, match="HTTP 503"):
        fetch.fetch_url(
            "https://acme.io/a", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(503)),
        )


def test_fetch_4xx_is_fatal(monkeypatch):
    # A 404 (and any non-429 4xx, or a non-redirect 3xx) is deterministic -> fatal.
    _stub_public_dns(monkeypatch)
    with pytest.raises(retry.FatalError, match="HTTP 404"):
        fetch.fetch_url(
            "https://acme.io/a", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(404)),
        )


def test_fetch_429_is_retryable_and_honors_retry_after(monkeypatch):
    # A 429 is retryable (REQ-Q3 backoff); its Retry-After delta-seconds surfaces
    # on the error so the worker won't retry sooner than the target asked.
    _stub_public_dns(monkeypatch)
    with pytest.raises(retry.RetryableError, match="HTTP 429") as excinfo:
        fetch.fetch_url(
            "https://acme.io/a", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(429, headers={"retry-after": "12"})),
        )
    assert excinfo.value.retry_after == 12.0


def test_fetch_429_without_retry_after_has_none(monkeypatch):
    _stub_public_dns(monkeypatch)
    with pytest.raises(retry.RetryableError) as excinfo:
        fetch.fetch_url(
            "https://acme.io/a", _SCOPE, timeout_s=5, max_bytes=1000,
            transport=_mock(lambda r: httpx.Response(429)),
        )
    assert excinfo.value.retry_after is None


def test_pin_dns_builds_sockaddr_and_fails_closed():
    # Directly exercise the pin's resolver: the MockTransport tests never trigger a
    # real getaddrinfo, so cover the security-critical sockaddr build + fail-closed.
    with fetch._pin_dns("acme.io", ("93.184.216.34",)):
        info = socket.getaddrinfo("acme.io", 443)
        assert info[0][0] == socket.AF_INET
        assert info[0][4] == ("93.184.216.34", 443)
        with pytest.raises(egress.EgressBlocked, match="unexpected DNS lookup"):
            socket.getaddrinfo("other.example", 443)
    # restored on exit
    assert socket.getaddrinfo is not None


def test_pin_dns_ipv6_sockaddr():
    with fetch._pin_dns("acme.io", ("2606:4700:4700::1111",)):
        info = socket.getaddrinfo("acme.io", 443)
        assert info[0][0] == socket.AF_INET6
        assert info[0][4] == ("2606:4700:4700::1111", 443, 0, 0)


@pytest.mark.integration
def test_fetch_run_stores_input_and_is_idempotent(redis, authorized_session, monkeypatch):
    from sqlalchemy import update

    from recon.db import models
    from recon.db.base import tenant_session
    from recon.runs import service

    tenant, session_id = authorized_session  # scope_hosts=["acme.io"], authorized
    view = service.create_run(
        redis, tenant_id=tenant, session_id=session_id, target="https://acme.io/app.js"
    )

    monkeypatch.setattr(fetch, "fetch_url", lambda url, scope, **kwargs: b'fetch("/api/x");')
    fetch.fetch_run(redis, tenant_id=tenant, run_id=view.id)

    with tenant_session(tenant) as session:
        run = session.get(models.Run, view.id)
        assert run.input_ref is not None

    # Idempotent: with input_ref already set, a retry must NOT re-fetch.
    def _must_not_fetch(*args, **kwargs):
        raise AssertionError("fetch_run re-fetched despite an existing input_ref")

    monkeypatch.setattr(fetch, "fetch_url", _must_not_fetch)
    fetch.fetch_run(redis, tenant_id=tenant, run_id=view.id)  # no-op, no exception


@pytest.mark.integration
def test_fetch_run_throttled_defers_without_fetching(redis, authorized_session, monkeypatch):
    # When politeness throttles the host (REQ-Q3), fetch_run raises a RetryableError
    # carrying the wait and never performs the fetch — the worker reschedules it.
    from recon.fetch import politeness
    from recon.runs import service

    tenant, session_id = authorized_session
    view = service.create_run(
        redis, tenant_id=tenant, session_id=session_id, target="https://acme.io/app.js"
    )
    monkeypatch.setattr(politeness, "check", lambda *args, **kwargs: 3.0)

    def _must_not_fetch(*args, **kwargs):
        raise AssertionError("throttled fetch_run must not perform a fetch")

    monkeypatch.setattr(fetch, "fetch_url", _must_not_fetch)
    with pytest.raises(retry.RetryableError) as excinfo:
        fetch.fetch_run(redis, tenant_id=tenant, run_id=view.id)
    assert excinfo.value.retry_after == 3.0


@pytest.mark.integration
def test_fetch_run_skips_non_url_target(redis, authorized_session):
    # A bare-host target is a scope label, not a fetch URL — fetch_run is a no-op
    # (keeps slice-1 runs that set target="acme.io" working).
    from recon.db import models
    from recon.db.base import tenant_session
    from recon.runs import service

    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")

    fetch.fetch_run(redis, tenant_id=tenant, run_id=view.id)  # must not raise or fetch

    with tenant_session(tenant) as session:
        assert session.get(models.Run, view.id).input_ref is None
