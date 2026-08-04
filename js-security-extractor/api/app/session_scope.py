"""Session scope helpers.

A session's scope is defined by ``root_domains`` (a list of root hostnames) plus
``include_subdomains``. These pure helpers derive/normalize root hostnames from URLs
and serialize the scope for API responses. Used by the sessions serializer, recon
job start, and the extension ingestion endpoint so they agree on the representation.
"""
from collections import Counter
from typing import Any, Iterable
from urllib.parse import urlparse


def host_of(url: str) -> str:
    """Root hostname for a URL or bare host: lowercased, scheme/userinfo/port/path
    stripped, leading ``www.`` removed. '' if unparseable. Accepts bare ``host``,
    ``host:port``, ``user@host`` and full URLs uniformly by parsing through urlparse
    (prefixing ``//`` for scheme-less input so it is read as a netloc, not a path)."""
    raw = str(url or "").strip()
    if raw and "://" not in raw and not raw.startswith("//"):
        raw = "//" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        host = ""
    return host[4:] if host.startswith("www.") else host


def derive_root_domains(urls: Iterable[str], limit: int = 5) -> list[str]:
    """Most-frequent root hostnames across a set of URLs (capped), commonest first."""
    counts: Counter = Counter()
    for url in urls:
        host = host_of(url)
        if host:
            counts[host] += 1
    return [host for host, _ in counts.most_common(limit)]


def normalize_root_domains(values: Iterable[Any], limit: int = 20) -> list[str]:
    """Clean a user/client-supplied root-domain list: accept bare hosts, host:port,
    user@host or full URLs, reduce each to its bare hostname (scheme/port/userinfo/
    path/``www.`` stripped), de-duplicate, drop blanks. Order-preserving."""
    out: list[str] = []
    for value in values or []:
        host = host_of(value)
        if host and host not in out:
            out.append(host)
        if len(out) >= limit:
            break
    return out


def scope_payload(session) -> dict[str, Any]:
    """Serialize a Session's scope for API responses."""
    return {
        "rootDomains": list(session.root_domains or []),
        "includeSubdomains": bool(session.include_subdomains),
    }
