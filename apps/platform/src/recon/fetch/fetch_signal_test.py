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


def test_allowlist_keeps_widened_fingerprint_headers():
    # DEBT D22: CSP / CORS / vendor identifiers are now kept (architecture signal).
    kept = fetch._allowlisted_headers(
        {
            "content-security-policy": "default-src 'self' https://cdn.acme.io",
            "access-control-allow-origin": "https://app.acme.io",  # CORS prefix rule
            "access-control-allow-credentials": "true",
            "x-cdn": "Incapsula",
            "powered-by": "PleskLin",
            "platform": "hostinger",
            "x-fastly-request-id": "r1",
        }
    )
    assert kept["content-security-policy"] == "default-src 'self' https://cdn.acme.io"
    assert kept["access-control-allow-origin"] == "https://app.acme.io"
    assert kept["access-control-allow-credentials"] == "true"
    assert kept["x-cdn"] == "Incapsula"
    assert kept["powered-by"] == "PleskLin"
    assert kept["platform"] == "hostinger"
    assert kept["x-fastly-request-id"] == "r1"


def test_allowlist_drops_link_and_all_credential_headers():
    # `link` is dropped: its URLs can carry signed-CDN query tokens (REQ-S2/S4). The
    # credential-bearing headers stay excluded at every widening level.
    kept = fetch._allowlisted_headers(
        {
            "server": "nginx",
            "link": "<https://cdn.acme.io/a.js?Signature=SECRET&Expires=1>; rel=preload",
            "authorization": "Bearer tok",
            "proxy-authorization": "Basic tok",
            "cookie": "sid=SECRET",
            "set-cookie": "sid=SECRET; Path=/",
        }
    )
    assert kept == {"server": "nginx"}


def test_allowlist_www_authenticate_stores_scheme_only():
    # A capture-path 401 challenge must not custody the NTLM/Negotiate type-2 blob
    # (internal AD/host names + nonce) — only the scheme token is kept.
    kept = fetch._allowlisted_headers({"www-authenticate": "NTLM TlRMTVNTUAABAAAAB4IIogAAAAAAAAAA"})
    assert kept == {"www-authenticate": "NTLM"}
    # empty/blank challenge must not IndexError — the `else ""` guard is load-bearing
    assert fetch._allowlisted_headers({"www-authenticate": ""}) == {"www-authenticate": ""}
    assert fetch._allowlisted_headers({"www-authenticate": "   "}) == {"www-authenticate": ""}


def test_allowlist_truncates_oversized_header_value():
    # A multi-KB header can't bloat the signal blob — kept values are size-bounded,
    # for BOTH an exact-match (CSP) and a prefix-match (CORS) header.
    big = "default-src 'self' " + "https://a.acme.io " * 5000
    kept = fetch._allowlisted_headers(
        {"content-security-policy": big, "access-control-allow-headers": "x-" * 20000}
    )
    assert len(kept["content-security-policy"]) == fetch._HEADER_VALUE_MAX_CHARS
    assert len(kept["access-control-allow-headers"]) == fetch._HEADER_VALUE_MAX_CHARS
