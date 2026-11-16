"""The built-in third-party rules: ONE list, shared with the capture extension (ADR-0010).

``third_party_rules.json`` holds the hosts (analytics, telemetry, ads, vendor CDNs, standards
references) and the FILE patterns (``*/jquery*.min.js``, ``/wp-content/plugins/*``, ...). The
extension keeps a generated copy (``apps/capture/chrome-extension/modules/third-party-rules.js``,
refreshed by ``scripts/gen-third-party-rules.mjs``) and a test fails on drift, so the two never
decide differently. The JSON also carries ``cases`` that BOTH sides run through their own matcher.

Two kinds of entry, deliberately different:

* a HOST entry never drops a host the researcher typed into the scope list (the caller checks scope);
* a FILE pattern applies on any host, scoped or not, because a library file on the app's own host is
  still a library.

The matcher mirrors the extension's ``matchesPattern`` (``modules/asset-classifier.js``): ``*`` is a
wildcard, matching is case-insensitive, a pattern starting with ``/`` is tested on the URL path, and
one containing ``/`` anywhere else on ``host + path`` or the path.
"""

from __future__ import annotations

import json
import re
from functools import cache, lru_cache
from importlib import resources
from typing import Any
from urllib.parse import urlsplit


@lru_cache(maxsize=1)
def _doc() -> dict[str, Any]:
    text = resources.files("recon.findings").joinpath("third_party_rules.json").read_text("utf-8")
    doc: dict[str, Any] = json.loads(text)
    return doc


def host_groups() -> dict[str, list[str]]:
    return {name: list(hosts) for name, hosts in _doc()["hosts"].items()}


HOSTS: frozenset[str] = frozenset(h for hosts in _doc()["hosts"].values() for h in hosts)
FILE_PATTERNS: tuple[str, ...] = tuple(_doc()["file_patterns"])
CASES: tuple[tuple[str, bool], ...] = tuple((url, bool(match)) for url, match in _doc()["cases"])


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    # Collapse runs of `*` (no catastrophic backtracking), escape everything else, `*` -> `.*`.
    collapsed = re.sub(r"\*+", "*", pattern.strip())
    return re.compile("^" + ".*".join(re.escape(part) for part in collapsed.split("*")) + "$", re.I)


def matches_pattern(url: str, pattern: str) -> bool:
    """The extension's ``matchesPattern``, in Python."""
    if not pattern:
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = (parts.path or "").lower()
    except ValueError:
        return False
    rx = _compile(pattern)
    if pattern.startswith("/"):
        return bool(rx.match(path))
    if "/" in pattern:
        return bool(rx.match(host + path) or rx.match(path))
    return bool(rx.match(host))


def matches_file_pattern(url: str | None) -> bool:
    """Whether ``url`` is a known third-party FILE (a library, a CMS plugin path, an analytics tag)."""
    if not url:
        return False
    return any(matches_pattern(url, pattern) for pattern in FILE_PATTERNS)
