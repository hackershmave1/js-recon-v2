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
from collections.abc import Iterable
from functools import lru_cache
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


_MAX_MATCHED_LENGTH = 2048  # a longer host/path is cut: the matcher is O(pattern x text)


def glob_match(pattern: str, text: str) -> bool:
    """``*`` wildcard match, case-insensitive, in O(len(pattern) x len(text)) with no regex.

    A team-supplied pattern is untrusted input (ADR-0010 R2): a regex built from ``a*a*a*a``
    backtracks polynomially on a long hostile path, so the matcher walks the text once with a single
    remembered star position instead. The extension has the same algorithm (``globMatch`` in
    ``modules/asset-classifier.js``), and the shared ``cases`` run through both."""
    p, s = pattern.lower(), text.lower()[:_MAX_MATCHED_LENGTH]
    pi = si = 0
    star = mark = -1
    while si < len(s):
        if pi < len(p) and p[pi] == "*":
            star, mark = pi, si
            pi += 1
        elif pi < len(p) and p[pi] == s[si]:
            pi += 1
            si += 1
        elif star != -1:
            mark += 1
            pi, si = star + 1, mark
        else:
            return False
    while pi < len(p) and p[pi] == "*":
        pi += 1
    return pi == len(p)


def matches_pattern(url: str, pattern: str) -> bool:
    """The extension's ``matchesPattern``, in Python."""
    pattern = pattern.strip()
    if not pattern:
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = (parts.path or "").lower()
    except ValueError:
        return False
    if pattern.startswith("/"):
        return glob_match(pattern, path)
    if "/" in pattern:
        return glob_match(pattern, host + path) or glob_match(pattern, path)
    return glob_match(pattern, host)


def matches_file_pattern(url: str | None, extra_patterns: Iterable[str] = ()) -> bool:
    """Whether ``url`` is a known third-party FILE (a library, a CMS plugin path, an analytics tag):
    a built-in pattern or one of the team's additions."""
    if not url:
        return False
    return any(matches_pattern(url, pattern) for pattern in (*FILE_PATTERNS, *extra_patterns))
