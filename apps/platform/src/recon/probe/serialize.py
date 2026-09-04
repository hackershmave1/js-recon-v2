"""Serialize a ReconstructedRequest to ready-to-fire artifacts (REQ-P1).

curl and raw HTTP are the slice-3a formats (raw HTTP covers the Burp Repeater
paste workflow). Both are pure functions over one request.

Security: the analyzed JS is attacker-influenced and these artifacts are pasted
into a shell (curl) or an HTTP client (raw HTTP). So curl shell-quotes every
interpolated value and raw HTTP strips CR/LF/control chars from every component —
neither artifact may become a shell-injection or header-injection vector.
"""

from __future__ import annotations

import json
import shlex
from urllib.parse import urlsplit

from recon.probe.reconstruct import ReconstructedRequest

_MAX_URL = 8192
_MAX_BODY = 65536
_BASE_URL_PLACEHOLDER = "{{base_url}}"
# WS/WSS operations (mirrors reconstruct._WEBSOCKET_METHODS) — curl/raw-HTTP don't apply, but
# to_websocat emits a runnable socket command for them (D51).
_WEBSOCKET_METHODS = frozenset({"WS", "WSS"})


def _control_free(text: str) -> str:
    """Drop control characters (< 0x20 and DEL) — the anti-injection primitive."""
    return "".join(ch for ch in text if 0x20 <= ord(ch) != 0x7F)


def _request_parts(request: ReconstructedRequest) -> tuple[str, str, str]:
    """Return (base, origin_target, host) for the artifact.

    Prefers the concrete observed URL. If it is ALREADY absolute
    (scheme://host/...), the host/scheme come from it directly — never
    re-prepended, which previously produced a double-scheme URL. If it is
    relative, the base is the occurrence host (or a {{base_url}} placeholder).
    origin_target is always origin-form (path + query) for the raw-HTTP request
    line; curl joins base + origin_target into a full URL.
    """
    observed = _control_free(request.example_url or request.path)[:_MAX_URL]
    split = urlsplit(observed)
    if split.scheme and split.netloc:
        host = split.netloc
        base = f"{split.scheme}://{host}"
        origin = (split.path or "/") + (f"?{split.query}" if split.query else "")
        return base, origin, host
    host = _control_free(request.hosts[0])[:_MAX_URL] if request.hosts else None
    base = f"https://{host}" if host else _BASE_URL_PLACEHOLDER
    return base, (observed or "/"), (host or "HOST")


def _json_body(request: ReconstructedRequest) -> str | None:
    if not request.body_params:
        return None
    body = {name: f"<{name}>" for name in request.body_params}
    return json.dumps(body, separators=(",", ":"))[:_MAX_BODY]


def _auth_headers(request: ReconstructedRequest) -> list[tuple[str, str]]:
    """(header-name, placeholder-value) for each observed auth header (D51).

    request.auth carries the auth headers seen for this operation as (name, scheme);
    the serializers used to print a static ``# add auth/headers here`` even when the
    scheme was known, so every authenticated endpoint's copied artifact 401'd. We emit
    a real placeholder per scheme (never a real secret — none is known): bearer/basic
    get their canonical prefix, anything else a ``<name>`` slot. Names are control-free
    + deduped; the value slots are literal placeholders (injection-free by construction).
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, scheme in request.auth:
        clean = _control_free(str(name))[:_MAX_URL]
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        normalized = (scheme or "").lower()
        if normalized == "bearer":
            value = "Bearer <token>"
        elif normalized == "basic":
            value = "Basic <base64(user:pass)>"
        else:
            value = f"<{clean}>"
        out.append((clean, value))
    return out


def to_curl(request: ReconstructedRequest) -> str | None:
    if not request.probeable:
        return None
    # Sanitize method (attacker-controlled via JS literals)
    method = _control_free(request.method)[:_MAX_URL]
    base, origin, _host = _request_parts(request)
    url = (base + origin)[:_MAX_URL]
    quoted_url = "'" + url.replace("'", "'\\''") + "'"
    # Cap host in comment (attacker-controlled via JS string literal)
    host_note = (
        f"  (host: {_control_free(request.hosts[0])[:_MAX_URL]})"
        if request.hosts
        else "  (host unknown)"
    )
    auth = _auth_headers(request)
    lines = [f"# {_control_free(request.operation)[:_MAX_URL]}{host_note}"]
    if not auth:
        lines.append("# add auth/headers here")
    curl = f"curl -X {shlex.quote(method)} {quoted_url}"
    extra: list[str] = []
    for name, value in auth:
        extra.append(f"-H {shlex.quote(name + ': ' + value)}")
    if request.content_type:
        extra.append(f"-H {shlex.quote('Content-Type: ' + _control_free(request.content_type))}")
    body = _json_body(request)
    if body:
        extra.append(f"--data {shlex.quote(body)}")
    if extra:
        lines.append(curl + " \\")
        for index, piece in enumerate(extra):
            lines.append("  " + piece + (" \\" if index < len(extra) - 1 else ""))
    else:
        lines.append(curl)
    if len(request.hosts) > 1:
        # Cap the whole "other hosts" line (hosts are attacker-controlled)
        other_hosts_line = (
            "# other hosts: " + ", ".join(_control_free(h) for h in request.hosts[1:])
        )[:_MAX_URL]
        lines.append(other_hosts_line)
    return "\n".join(lines)


def to_http(request: ReconstructedRequest) -> str | None:
    if not request.probeable:
        return None
    base, origin, host = _request_parts(request)
    method = _control_free(request.method)[:_MAX_URL]
    lines = [
        f"{method} {origin} HTTP/1.1",
        f"Host: {host}",
    ]
    auth = _auth_headers(request)
    if auth:
        lines.extend(f"{name}: {value}" for name, value in auth)
    else:
        lines.append("# add auth/headers here")
    if request.content_type:
        lines.append(f"Content-Type: {_control_free(request.content_type)}")
    lines.append("")
    lines.append(_json_body(request) or "")
    return "\n".join(lines)


def to_websocat(request: ReconstructedRequest) -> str | None:
    """A ``websocat`` scaffold for a WS/WSS operation (D51).

    WebSocket ops are not HTTP requests, so to_curl/to_http return None and the UI used
    to dead-end at "not probeable". A reviewer still wants a one-line command to open the
    socket, so emit one — with the observed auth headers as ``-H`` flags. Returns None for
    non-WebSocket requests (the caller falls back to curl/http).
    """
    if request.method not in _WEBSOCKET_METHODS:
        return None
    base, origin, _host = _request_parts(request)
    url = (base + origin)[:_MAX_URL]
    # _request_parts yields the observed scheme (often already ws/wss) or an https/base_url
    # fallback; force the websocket scheme so the command is runnable as-is.
    if url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    quoted_url = "'" + url.replace("'", "'\\''") + "'"
    host_note = (
        f"  (host: {_control_free(request.hosts[0])[:_MAX_URL]})"
        if request.hosts
        else "  (host unknown)"
    )
    parts = ["websocat"]
    for name, value in _auth_headers(request):
        parts.append(f"-H {shlex.quote(name + ': ' + value)}")
    parts.append(quoted_url)
    return "\n".join(
        [f"# {_control_free(request.operation)[:_MAX_URL]}{host_note}", " ".join(parts)]
    )
