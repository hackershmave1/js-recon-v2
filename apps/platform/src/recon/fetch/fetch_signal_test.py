"""Tests for the fetch-stage fingerprint-signal harvest (Task 6).

Pure — httpx MockTransport only, no infra. Proves the shared hop-core
(``_fetch_hops``) returns body+status+headers, that ``fetch_url`` is unchanged
behind it (T5), and that the allowlist/cookie-name helpers never leak a
credential-bearing header or a cookie value (T1).

DNS is stubbed (mirrors ``fetch_test.py``'s ``_stub_public_dns``): the mock
transport replaces the HTTP round-trip, but ``egress.validate_target`` still
does a REAL ``socket.getaddrinfo`` first — leaving it unstubbed would make
these tests depend on ``acme.io`` actually resolving from the test host.
"""

from __future__ import annotations

import socket

import httpx

from recon.fetch import fetch


def _stub_public_dns(monkeypatch):
    def resolver(host, service, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", service))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)


def _transport(headers: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, content=b"console.log(1)")

    return httpx.MockTransport(handler)


def test_fetch_hops_returns_body_status_and_headers(monkeypatch):
    _stub_public_dns(monkeypatch)
    resp = fetch._fetch_hops(
        "https://acme.io/app.js",
        ["acme.io"],
        timeout_s=5,
        max_bytes=1_000_000,
        transport=_transport({"Server": "nginx/1.25.3", "X-Powered-By": "Express"}),
    )
    assert resp.body == b"console.log(1)"
    assert resp.status == 200
    assert resp.headers["server"] == "nginx/1.25.3"


def test_fetch_url_still_returns_bytes_unchanged(monkeypatch):
    _stub_public_dns(monkeypatch)
    body = fetch.fetch_url(
        "https://acme.io/app.js",
        ["acme.io"],
        timeout_s=5,
        max_bytes=1_000_000,
        transport=_transport({"Server": "nginx"}),
    )
    assert body == b"console.log(1)"


def test_allowlist_keeps_fingerprint_headers_and_drops_the_rest():
    kept = fetch._allowlisted_headers(
        {
            "server": "nginx",
            "x-powered-by": "Express",
            "authorization": "Bearer x",
            "set-cookie": "sid=abc",
            "x-fastly-request-id": "r1",
        }
    )
    assert kept == {"server": "nginx", "x-powered-by": "Express", "x-fastly-request-id": "r1"}
    assert "authorization" not in kept and "set-cookie" not in kept


def test_cookie_names_never_carry_values():
    assert fetch._cookie_names(["sid=SECRETVALUE; Path=/", "theme=dark"]) == ["sid", "theme"]
