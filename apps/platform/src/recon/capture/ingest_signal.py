"""The extension-ingest fingerprint-signal producer (DEBT D77).

The crawl (``recon.fetch.fetch``) and the server-side capture (``recon.capture.stage``)
each write a host-keyed ``fingerprint-signal`` blob that the analyze tech-detection pass
reads. An extension capture never did, so it never got a tech stack. The extension
already uploads every file's response ``headers`` (lowercased, from
``chrome.webRequest``) next to its URL and bytes; this builds the SAME signal schema
from one ``/api/save-files`` batch, through the SAME allowlist choke point as the other
two producers, so the existing pass runs unchanged.

Scope: like the server-side capture producer (``capture.stage._build_signal``), a file served
by a host outside the session's DECLARED scope contributes nothing — its headers, cookie names
and URL are never persisted (T1), so an ad/analytics/CDN third party the page loaded gets no
tech-stack row. With no declared scope there is nothing to be outside of, and every file counts.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from recon.fetch import egress
from recon.fetch.fetch import _allowlisted_headers, _cookie_names


def build_batch_signal(
    files: list[tuple[str, Any]],
    *,
    scope_hosts: list[str] | None = None,
    allow_local: bool = False,
) -> dict[str, dict]:
    """``{host: {headers, scripts, meta, cookies}}`` for one batch of stored files, given
    each file's ``(url, uploaded headers)``; ``scope_hosts`` (``None`` = none declared) drops
    every file served by an off-scope host. TOTAL: the headers come from an untrusted
    client, so a malformed url or headers value is skipped, never raised (a raise here
    would 5xx an otherwise durable batch). ``meta`` stays empty — the extension uploads
    scripts, not the HTML document a ``<meta generator>`` lives in."""
    signal: dict[str, dict] = {}
    for url, raw_headers in files:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue  # e.g. an unterminated IPv6 literal
        if not host:
            continue
        if scope_hosts is not None and not egress.host_in_scope(
            host, scope_hosts, allow_local=allow_local
        ):
            continue
        entry = signal.setdefault(host, {"headers": {}, "scripts": [], "meta": [], "cookies": []})
        headers = _lowercased(raw_headers)
        entry["headers"].update(_allowlisted_headers(headers))
        # Best-effort: the extension keeps one value per header name (the last), and Chrome
        # may withhold Set-Cookie from webRequest without "extraHeaders", so cookie names are
        # often absent. Split on "\n" in case a client sends the CDP-style joined form.
        set_cookie = headers.get("set-cookie")
        if set_cookie:
            for name in _cookie_names(set_cookie.split("\n")):
                if name not in entry["cookies"]:
                    entry["cookies"].append(name)
        if url not in entry["scripts"]:
            entry["scripts"].append(url)
    for entry in signal.values():
        entry["cookies"].sort()
    return signal


def _lowercased(raw_headers: Any) -> dict[str, str]:
    """String-keyed, lowercased headers; anything that is not a dict yields ``{}``."""
    if not isinstance(raw_headers, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in raw_headers.items() if isinstance(k, str)}
