"""Unit tests for the egress/SSRF guard (pure; DNS is stubbed)."""

from __future__ import annotations

import socket

import pytest

from recon.fetch import egress
from recon.fetch.egress import EgressBlocked

_SCOPE = ["acme.io", "cdn.acme.io"]


def _fake_getaddrinfo(ip: str):
    def resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    return resolver


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "::1", "::ffff:127.0.0.1",  # loopback (incl. IPv4-mapped)
        "10.0.0.5", "172.16.0.1", "192.168.1.1",  # private
        "169.254.169.254", "fd00:ec2::254",  # cloud metadata (link-local / ULA)
        "0.0.0.0",  # unspecified
        "224.0.0.1", "ff02::1",  # multicast (IPv4 + IPv6) — report is_global=True
        "192.0.2.1",  # reserved (TEST-NET, is_reserved/is_private)
        "100.64.0.1",  # CGNAT (RFC 6598) — leaks past an enumerated deny-list
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1 — is_global but reserved
    ],
)
def test_is_public_ip_blocks_dangerous(ip):
    assert egress.is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_is_public_ip_allows_global(ip):
    assert egress.is_public_ip(ip) is True


def test_is_public_ip_rejects_garbage():
    assert egress.is_public_ip("not-an-ip") is False


def test_host_in_scope_matches_host_and_subdomains():
    assert egress.host_in_scope("acme.io", _SCOPE) is True
    assert egress.host_in_scope("CDN.Acme.IO", _SCOPE) is True  # case-insensitive
    assert egress.host_in_scope("acme.io.", _SCOPE) is True  # trailing dot
    assert egress.host_in_scope("sub.acme.io", _SCOPE) is True  # subdomain now in scope
    assert egress.host_in_scope("a.b.acme.io", _SCOPE) is True  # deep subdomain
    assert egress.host_in_scope("", _SCOPE) is False


def test_host_in_scope_rejects_suffix_tricks():
    # dot-boundary suffix only — no substring / sibling-domain matches.
    assert egress.host_in_scope("evil-acme.io", _SCOPE) is False
    assert egress.host_in_scope("notacme.io", _SCOPE) is False
    assert egress.host_in_scope("acme.io.evil.com", _SCOPE) is False  # suffix is .evil.com
    assert egress.host_in_scope("acme.io.attacker.net", _SCOPE) is False


def test_host_in_scope_drops_overbroad_entries():
    # A subdomain rule under a bare TLD / public suffix / IP would authorize the
    # internet, so such entries are dropped and authorize nothing (fail closed).
    assert egress.host_in_scope("evil.com", ["com"]) is False
    assert egress.host_in_scope("anything.io", ["io"]) is False
    assert egress.host_in_scope("x.localhost", ["localhost"]) is False
    assert egress.host_in_scope("victim.github.io", ["github.io"]) is False
    assert egress.host_in_scope("x.co.uk", ["co.uk"]) is False
    assert egress.host_in_scope("10.0.0.1", ["10.0.0.1"]) is False
    assert egress.host_in_scope("acme.io", ["  ", ""]) is False
    # a valid entry still authorizes even when mixed with rejected ones.
    assert egress.host_in_scope("x.acme.io", ["com", "acme.io"]) is True


def test_is_valid_scope_entry():
    for good in ["acme.io", "cdn.acme.io", "ACME.IO", "acme.io.", "a.b.c.example.com"]:
        assert egress.is_valid_scope_entry(good) is True, good
    for bad in [
        "", "   ", "com", "io", "localhost", "internal",  # empty / single-label
        "co.uk", "github.io", "s3.amazonaws.com",  # public suffixes
        "10.0.0.1", "127.0.0.1",  # IP literals
        "acme.io:8443", "https://acme.io", "acme.io/app", "a@b.com", "*.acme.io", "ac me.io",
    ]:
        assert egress.is_valid_scope_entry(bad) is False, bad


def test_validate_target_allows_in_scope_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    target = egress.validate_target("https://acme.io/app.js", _SCOPE)
    assert target.host == "acme.io" and target.ips == ("93.184.216.34",)


def test_validate_target_blocks_out_of_scope(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    with pytest.raises(EgressBlocked, match="scope"):
        egress.validate_target("https://evil.example/app.js", _SCOPE)


def test_validate_target_blocks_in_scope_resolving_to_private(monkeypatch):
    # DNS says an in-scope host points at an internal IP -> blocked (SSRF).
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    with pytest.raises(EgressBlocked, match="non-public"):
        egress.validate_target("https://acme.io/meta", _SCOPE)


def test_validate_target_blocks_bad_scheme():
    with pytest.raises(EgressBlocked, match="scheme"):
        egress.validate_target("file:///etc/passwd", _SCOPE)
    with pytest.raises(EgressBlocked, match="scheme"):
        egress.validate_target("gopher://acme.io/", _SCOPE)


def test_validate_target_blocks_userinfo(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    # The real host is evil.example; also reject userinfo outright.
    with pytest.raises(EgressBlocked):
        egress.validate_target("https://acme.io@evil.example/app.js", _SCOPE)


def test_validate_target_blocks_decimal_ip_literal(monkeypatch):
    # 2130706433 == 127.0.0.1; getaddrinfo normalizes it, is_public_ip rejects it.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    with pytest.raises(EgressBlocked):
        egress.validate_target("http://acme.io/", _SCOPE)  # scope ok, IP private
