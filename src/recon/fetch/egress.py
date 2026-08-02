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
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# One DNS label: 1-63 chars of letters/digits/hyphen, not leading/trailing hyphen
# (LDH rule). An allowlist, not a denylist — so control chars, whitespace, and any
# punctuation are rejected structurally rather than needing enumeration.
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

# Multi-label public suffixes where subdomain-scoping would authorize hosts the
# engagement never declared (e.g. every ``*.github.io`` site). NOT a full public-
# suffix list — just common shared-hosting suffixes; a bare single-label TLD
# (``com``) is already rejected structurally (no dot) by is_valid_scope_entry.
_PUBLIC_SUFFIX_DENYLIST = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "com.au", "com.br", "co.in", "co.za",
    "github.io", "gitlab.io", "herokuapp.com", "web.app", "firebaseapp.com",
    "pages.dev", "workers.dev", "netlify.app", "vercel.app", "now.sh",
    "cloudfront.net", "amazonaws.com", "s3.amazonaws.com", "azurewebsites.net",
    "blob.core.windows.net", "appspot.com", "run.app", "blogspot.com",
    "wordpress.com", "glitch.me", "repl.co", "surge.sh",
})


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


def _normalize_host(value: str | None) -> str:
    """Lowercase + strip surrounding whitespace and the FQDN-root trailing dot."""
    return (value or "").strip().rstrip(".").lower()


def is_valid_scope_entry(entry: str) -> bool:
    """Whether a declared scope entry is specific enough to authorize a host + its
    subdomains without authorizing half the internet. Invalid entries are dropped
    from the allow-set so egress fails closed no matter how ``scope_hosts`` was
    populated (create, rerun, default-from-target, a direct DB write).

    Requires >= 2 LDH labels (letters/digits/hyphen). This rejects, structurally:
    empty/whitespace; anything carrying a scheme, port, path, userinfo, wildcard,
    control char, or whitespace (not a bare host); an empty label (``a..b``,
    leading/trailing dot); a single-label name (a bare TLD like ``com`` or an
    internal name like ``localhost``); an IP literal, including dotted-decimal and
    short forms like ``127.1`` (a real host's final label is never all-digits — IPs
    are judged by :func:`is_public_ip`, not scope); and a known multi-label public
    suffix (``github.io``, ``co.uk`` …), under which a subdomain rule would
    authorize every tenant of that shared host.
    """
    host = _normalize_host(entry)
    if not host:
        return False
    labels = host.split(".")
    if len(labels) < 2:  # single label -> bare TLD or internal name
        return False
    if not all(_LABEL_RE.match(label) for label in labels):
        return False  # a non-LDH char, or an empty label
    if labels[-1].isdigit():
        return False  # all-numeric final label -> an IPv4 literal / short form
    return host not in _PUBLIC_SUFFIX_DENYLIST


def normalize_scope_entry(entry: str) -> str | None:
    """The normalized host for a VALID user-supplied scope entry, or ``None`` if it
    is not a usable host-scope declaration (see :func:`is_valid_scope_entry`).

    Accepts a leading ``*.`` wildcard and reduces it to the base host: a bare host
    already authorizes its subdomains (S1), so ``*.acme.io`` and ``acme.io`` are
    equivalent in scope. A bare ``*`` (match anything) is NOT accepted — that is the
    deferred unrestricted-crawl case. Lets a caller persist a clean, deduped
    allow-list and reject a bad entry at the edge (a create-time 400) instead of
    silently dropping it at egress time; the egress decision fails closed on its own,
    so this is a UX/hygiene layer, not the security boundary."""
    host = _normalize_host(entry)
    if host.startswith("*."):
        host = host[2:]  # "*.acme.io" -> "acme.io"; the bare host covers subdomains
    if not is_valid_scope_entry(host):
        return None
    return host


def host_of(target: str | None) -> str:
    """The lowercased host of a user-supplied target — a bare domain (``acme.io``)
    or a full URL (``https://acme.io:8443/x``) — or ``""`` if none. The single
    host-extraction used for scope decisions that start from a target (defaulting
    a blank scope, the API's fail-fast crawl-scope check); the fetch-time seed
    guard keeps its own URL form for the DNS/public-IP check."""
    if not target or not target.strip():
        return ""
    t = target if "://" in target else f"https://{target.strip()}"
    try:
        return (urlsplit(t).hostname or "").lower()
    except ValueError:
        return ""  # malformed (e.g. a bad IPv6 literal) -> no host -> out of scope


def host_in_scope(host: str | None, scope_hosts: list[str]) -> bool:
    """True if ``host`` equals, or is a subdomain of, a VALID declared scope entry
    (REQ-P2). Subdomain = an exact dot-boundary suffix: ``acme.io`` authorizes
    ``acme.io`` and ``*.acme.io`` but never ``evil-acme.io`` (no dot boundary) or
    ``acme.io.evil.com`` (its suffix is ``.evil.com``). Over-broad / malformed
    entries are dropped (see :func:`is_valid_scope_entry`), so the egress decision
    fails closed regardless of how ``scope_hosts`` was set.
    """
    normalized = _normalize_host(host)
    if not normalized:
        return False
    allowed = {_normalize_host(e) for e in scope_hosts if is_valid_scope_entry(e)}
    return any(normalized == entry or normalized.endswith("." + entry) for entry in allowed)


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
    if isinstance(ip, ipaddress.IPv6Address):
        # Unwrap an embedded IPv4 and judge THAT, or an internal address hides
        # behind an is_global=True wrapper. ::ffff:127.0.0.1 (IPv4-mapped) and
        # 2002:a9fe:a9fe:: (6to4 of 169.254.169.254) both report is_global=True but
        # route to the embedded IPv4. Teredo (2001::/32) is already is_global=False.
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
    return ip.is_global and not ip.is_reserved and not ip.is_multicast


def validate_target(url: str, scope_hosts: list[str]) -> ValidatedTarget:
    """Enforce the full policy on ``url`` or raise :class:`EgressBlocked`.

    Resolves the host and requires ALL resolved addresses to be public — a single
    internal address (e.g. a split-horizon or rebinding record) blocks the fetch.
    """
    try:
        # urlsplit AND every netloc-derived access can raise ValueError on a
        # malformed URL — a bad IPv6 literal (http://[), an out-of-range port
        # (:99999). Fold them into the guard's own EgressBlocked so a crafted
        # target is a clean block, not an uncaught 500 / retry crash-loop.
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        username, password = parts.username, parts.password
        host = parts.hostname  # excludes port + userinfo; strips IPv6 brackets; lowercased
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise EgressBlocked(f"malformed URL: {exc}") from exc
    if scheme not in _ALLOWED_SCHEMES:
        raise EgressBlocked(f"scheme not allowed: {parts.scheme!r}")
    if username or password:
        # http://acme.io@evil.example/ — urlsplit.hostname is 'evil.example', but
        # reject userinfo outright so no credential-confusion trick gets close.
        raise EgressBlocked("userinfo is not allowed in a fetch URL")
    if not host:
        raise EgressBlocked("missing host in URL")
    if not host_in_scope(host, scope_hosts):
        raise EgressBlocked(f"host not in engagement scope: {host}")

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
