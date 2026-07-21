"""Application-level egress guard for the fetch stage (REQ-P2 + SSRF defense).

An outbound fetch may reach a URL only if:
  1. its scheme is http/https,
  2. its host is one the session explicitly declared in scope (REQ-P2: egress
     scope comes from ``session.scope_hosts``, never from crawled content), and
  3. every IP the host resolves to is globally routable — so a hostile target
     can't point the fetcher at loopback/private/link-local/cloud-metadata
     addresses and turn it into an SSRF pivot into our own infrastructure.

This is the MVP application-level guard. OS/network-level egress isolation
(egress proxy / network namespace / firewall) is deferred — a fuller defense,
noted in the fetch stage. DNS-rebinding is handled at fetch time by pinning the
connection to the IP validated here (see ``recon.fetch.fetch``).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class EgressBlocked(Exception):
    """A fetch target failed the scope or SSRF policy."""


@dataclass(frozen=True)
class ValidatedTarget:
    """A URL that passed the guard, with the exact public IPs it resolved to —
    the fetcher pins its connection to one of these to defeat DNS rebinding."""

    url: str
    host: str
    port: int
    ips: tuple[str, ...]


def host_in_scope(host: str | None, scope_hosts: list[str]) -> bool:
    """Exact, case-insensitive host match against the declared scope (REQ-P2).

    A trailing dot (the FQDN root) is normalized away. Match is exact, not
    suffix — ``acme.io`` in scope does NOT authorize ``evil-acme.io`` or an
    arbitrary subdomain, which keeps the egress surface exactly what was declared.
    """
    normalized = (host or "").strip().rstrip(".").lower()
    allowed = {entry.strip().rstrip(".").lower() for entry in scope_hosts if entry}
    return bool(normalized) and normalized in allowed


def is_public_ip(ip_str: str) -> bool:
    """True only for a globally-routable address. IPv4-mapped IPv6 is unwrapped
    so ``::ffff:127.0.0.1`` is judged as the loopback IPv4 it really is.

    Rule is ``is_global and not is_reserved and not is_multicast``: an enumerated
    deny-list of is_private/loopback/link_local/… leaks CGNAT (100.64/10 is
    neither private nor reserved); a bare ``not is_global`` leaks NAT64
    (64:ff9b::/96 is is_global but reserved); and some multicast (224.0.0.1,
    ff02::1) reports is_global=True, so it must be excluded too. Together these
    block every dangerous case while allowing plain public addresses. The rule
    reads the interpreter's special-purpose registry, so the table-driven tests
    pin the behavior against a CPython bump silently widening the allowlist.
    """
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_reserved and not ip.is_multicast


def validate_target(url: str, scope_hosts: list[str]) -> ValidatedTarget:
    """Enforce the full policy on ``url`` or raise :class:`EgressBlocked`.

    Resolves the host and requires ALL resolved addresses to be public — a single
    internal address (e.g. a split-horizon or rebinding record) blocks the fetch.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise EgressBlocked(f"scheme not allowed: {parts.scheme!r}")
    if parts.username or parts.password:
        # http://acme.io@evil.example/ — urlsplit.hostname is 'evil.example', but
        # reject userinfo outright so no credential-confusion trick gets close.
        raise EgressBlocked("userinfo is not allowed in a fetch URL")
    host = parts.hostname  # excludes port + userinfo; strips IPv6 brackets; lowercased
    if not host:
        raise EgressBlocked("missing host in URL")
    if not host_in_scope(host, scope_hosts):
        raise EgressBlocked(f"host not in engagement scope: {host}")
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EgressBlocked(f"DNS resolution failed for {host}") from exc
    ips = tuple(sorted({info[4][0] for info in infos}))
    if not ips:
        raise EgressBlocked(f"no addresses resolved for {host}")
    for ip in ips:
        if not is_public_ip(ip):
            raise EgressBlocked(f"host {host} resolves to a non-public address: {ip}")
    return ValidatedTarget(url=url, host=host, port=port, ips=ips)
