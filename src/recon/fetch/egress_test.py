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


def test_host_in_scope_exact_only():
    assert egress.host_in_scope("acme.io", _SCOPE) is True
    assert egress.host_in_scope("CDN.Acme.IO", _SCOPE) is True  # case-insensitive
    assert egress.host_in_scope("acme.io.", _SCOPE) is True  # trailing dot
    assert egress.host_in_scope("evil-acme.io", _SCOPE) is False  # not a suffix match
    assert egress.host_in_scope("sub.acme.io", _SCOPE) is False  # subdomain not implied
    assert egress.host_in_scope("", _SCOPE) is False


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
