"""katana argv construction + JSONL parsing (discovery-only).

We drive katana purely as a JS-asset *discovery* crawler: it enumerates URLs,
and our own Vespasian parses them later — so we never pass ``-jc``. Flags drift
between katana releases; re-verify against the vendored version (``katana -h``)
and capture parse fixtures from real output. The JSON field carrying the crawled
URL is ``request.endpoint`` (top-level ``endpoint`` as a fallback); confirm
against the vendored katana's JSONL.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit


def build_argv(
    *,
    katana_bin: str,
    domain: str,
    scope_hosts: list[str],
    depth: int,
    crawl_duration_seconds: float,
    system_chrome_path: str,
) -> list[str]:
    target = domain if "://" in domain else f"https://{domain}"
    argv = [
        katana_bin, "-u", target,
        "-headless", "-no-sandbox",
        "-system-chrome", "-system-chrome-path", system_chrome_path,
        "-jsonl", "-silent", "-em", "js",
        "-depth", str(depth),
        "-crawl-duration", f"{crawl_duration_seconds:g}",
        "-field-scope", "rdn",
    ]
    for host in scope_hosts:
        argv += ["-crawl-scope", host]
    return argv


def parse_assets(stdout: bytes) -> list[str]:
    seen: dict[str, None] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        url = _extract_url(row)
        if url is None:
            continue
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            continue
        if not parts.path.lower().endswith(".js"):
            continue
        seen.setdefault(url, None)
    return list(seen)


def _extract_url(row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    request = row.get("request")
    if isinstance(request, dict) and isinstance(request.get("endpoint"), str):
        return request["endpoint"]
    endpoint = row.get("endpoint")
    return endpoint if isinstance(endpoint, str) else None
