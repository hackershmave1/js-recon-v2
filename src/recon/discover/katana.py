"""katana argv construction + JSONL parsing (discovery-only).

We drive katana purely as a JS-asset *discovery* crawler: it enumerates URLs,
and our own Vespasian parses them later — so we never pass ``-jc``. We also
never pass ``-em`` (extension match): ``-em js`` was tried and empirically
filtered out *everything* katana would otherwise find, and it is redundant
anyway since ``parse_assets`` below already keeps only ``.js`` URLs. Crawl mode
defaults to standard (non-headless), which is the proven-working path; headless
is opt-in via ``crawl_headless`` (see the inline comment below for
why). Flags drift between katana releases; re-verify against the vendored
version (``katana -h``) and capture parse fixtures from real output. The JSON
field carrying the crawled URL is ``request.endpoint`` (top-level ``endpoint``
as a fallback); confirm against the vendored katana's JSONL.
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
    headless: bool = False,
    system_chrome_path: str | None = None,
) -> list[str]:
    target = domain if "://" in domain else f"https://{domain}"
    argv = [
        katana_bin, "-u", target,
        "-jsonl", "-silent",
        "-depth", str(depth),
        "-crawl-duration", f"{crawl_duration_seconds:g}",
        "-field-scope", "rdn",
    ]
    for host in scope_hosts:
        argv += ["-crawl-scope", host]
    if headless:
        # --disable-dev-shm-usage is required or headless Chrome hangs on Docker's
        # 64MB /dev/shm; -no-sandbox because the worker runs unprivileged.
        argv += ["-headless", "-no-sandbox", "-headless-options", "--disable-dev-shm-usage"]
        # Point katana at the system chromium baked into the image
        # (config.system_chrome_path = /usr/bin/chromium) instead of letting go-rod
        # download its own ~150MB Chromium from a Google CDN on every fresh container.
        # Verified 2026-08-02 (katana v1.6.1 + chromium 150): `-system-chrome-path`
        # launches instantly. The prior "go-rod rejects the system path" note predated
        # chromium being installed at this path and no longer holds.
        if system_chrome_path:
            argv += ["-system-chrome-path", system_chrome_path]
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
