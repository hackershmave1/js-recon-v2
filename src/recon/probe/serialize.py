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

from recon.probe.reconstruct import ReconstructedRequest

_MAX_URL = 8192
_MAX_BODY = 65536
_BASE_URL_PLACEHOLDER = "{{base_url}}"


def _control_free(text: str) -> str:
    """Drop control characters (< 0x20 and DEL) — the anti-injection primitive."""
    return "".join(ch for ch in text if 0x20 <= ord(ch) != 0x7f)


def _base_url(request: ReconstructedRequest) -> str:
    if request.hosts:
        return f"https://{_control_free(request.hosts[0])}"
    return _BASE_URL_PLACEHOLDER


def _target(request: ReconstructedRequest) -> str:
    # Prefer the concrete observed URL (ready-to-fire); fall back to templated path.
    return _control_free(request.example_url or request.path)[:_MAX_URL] or "/"


def _json_body(request: ReconstructedRequest) -> str | None:
    if not request.body_params:
        return None
    body = {name: f"<{name}>" for name in request.body_params}
    return json.dumps(body, separators=(",", ":"))[:_MAX_BODY]


def to_curl(request: ReconstructedRequest) -> str | None:
    if not request.probeable:
        return None
    url = _base_url(request) + _target(request)
    # Ensure URL is always quoted for consistency and safety
    quoted_url = shlex.quote(url)
    if not quoted_url.startswith(("'", '"')):
        quoted_url = f"'{url}'"
    host_note = f"  (host: {_control_free(request.hosts[0])})" if request.hosts else "  (host unknown)"
    lines = [
        f"# {_control_free(request.operation)}{host_note}",
        "# add auth/headers here",
    ]
    curl = f"curl -X {shlex.quote(request.method)} {quoted_url}"
    extra: list[str] = []
    if request.content_type:
        extra.append(f"-H {shlex.quote('Content-Type: ' + request.content_type)}")
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
        lines.append("# other hosts: " + ", ".join(_control_free(h) for h in request.hosts[1:]))
    return "\n".join(lines)


def to_http(request: ReconstructedRequest) -> str | None:
    if not request.probeable:
        return None
    host = _control_free(request.hosts[0]) if request.hosts else "HOST"
    lines = [
        f"{request.method} {_target(request)} HTTP/1.1",
        f"Host: {host}",
        "# add auth/headers here",
    ]
    if request.content_type:
        lines.append(f"Content-Type: {request.content_type}")
    lines.append("")
    lines.append(_json_body(request) or "")
    return "\n".join(lines)
