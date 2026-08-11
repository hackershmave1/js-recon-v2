"""Pure correlation of runtime-observed request URLs to static endpoint findings (REQ-C3).

Static extraction leaves an endpoint's host as an unresolved runtime variable
(``GET /${baseDomainName}/get-job-types``) or absent (``POST /getJobId``). Runtime
capture records the URL the browser actually issued
(``https://api.acme.io/get-job-types``). This module matches the two on their shared
CONSTANT path segments and returns the real URL to attach to the finding — host recovery
from ground truth, not a re-based guess.

Matching is deliberately conservative (honesty is a MUST, REQ-C2), tuned by the §4
adversarial review:

- a leading ``${var}`` absorbs the observed base ONLY when it is the leading segment;
- a rooted-constant path (``/getJobId``, ``/users``) must align EXACTLY, same segment
  count — so ``/users`` never grabs an observed ``/admin/users``;
- alignment is positional and contiguous (a param matches exactly one segment) — not a
  subsequence, so ``/orders/{id}/items`` never grabs ``/orders/x/y/items/z``;
- a template with no constant anchor (all vars/params) is never matched;
- the MOST-SPECIFIC finding wins a contested observed URL (most constants, least base
  absorbed); a tie is ambiguous and dropped;
- a finding that matches inconsistent URLs (two different hosts) is skipped, not guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_CLEAN_VAR = re.compile(r"^\$\{[^}]*\}$")  # a raw template var kept verbatim: ${baseDomainName}
_PLACEHOLDER = re.compile(r"^\{[^}]*\}$")  # a normalized path param: {id}


@dataclass(frozen=True)
class Endpoint:
    """A static endpoint finding to resolve: its hash + parsed HTTP operation."""

    finding_hash: str
    method: str
    path: str  # e.g. "/${baseDomainName}/get-job-types" or "/getJobId"


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _is_param(seg: str) -> bool:
    """A wildcard segment — a normalized ``{id}`` OR a raw ``${var}`` — matches any one
    observed segment. A MIXED segment (``v${n}``) is neither: it stays a literal CONST
    that an observed concrete value won't equal, so such a finding simply goes unresolved
    (safe: no false positive)."""
    return bool(_PLACEHOLDER.match(seg) or _CLEAN_VAR.match(seg))


def _parse_tail(path: str) -> tuple[list[str], bool] | None:
    """``(tail, host_templated)``. ``host_templated`` is True when the leading segment is
    a clean ``${var}`` (it stands for the host/base and is stripped from the tail).
    Returns ``None`` when the tail has no CONSTANT anchor to match on."""
    segs = _segments(path)
    if not segs:
        return None
    host_templated = bool(_CLEAN_VAR.match(segs[0]))
    tail = segs[1:] if host_templated else segs
    if not any(not _is_param(s) for s in tail):
        return None  # only vars/params — no constant anchor, unsafe to match
    return tail, host_templated


def _absorbed(tail: list[str], obs: list[str], host_templated: bool) -> int | None:
    """If ``obs`` aligns with ``tail``, return the count of leading observed segments
    absorbed as base (0 for a rooted path); else ``None``.

    - rooted (``host_templated`` False): ``obs`` must EQUAL ``tail`` exactly (same count);
    - host-templated: the LAST ``len(tail)`` observed segments align with ``tail`` and any
      leading remainder is the recovered base.
    A CONST tail segment must equal the observed segment; a param matches any one."""
    if host_templated:
        if len(obs) < len(tail):
            return None
        absorbed = len(obs) - len(tail)
        window = obs[absorbed:]
    else:
        if len(obs) != len(tail):
            return None
        absorbed = 0
        window = obs
    for t, o in zip(tail, window, strict=True):
        if not _is_param(t) and t != o:
            return None
    return absorbed


@dataclass(frozen=True)
class _Candidate:
    finding_hash: str
    resolved_url: str
    const_count: int
    absorbed: int


def _specificity(candidate: _Candidate) -> tuple[int, int]:
    """Rank a candidate: more matched constants first, then less base absorbed. A rooted
    exact match (``/orders/{id}/items`` on ``/orders/42/items``) thus beats a leading-var
    match that swallowed the same segments (``/${var}/items``)."""
    return (candidate.const_count, -candidate.absorbed)


def correlate(endpoints: list[Endpoint], requests: list[dict]) -> dict[str, str]:
    """Return ``{finding_hash: resolved_url}`` for endpoints confidently matched to an
    observed request. ``requests`` is ``[{"method", "url"}]`` with ``url`` already
    normalized to ``scheme://host/path`` (query dropped)."""
    # 1. Gather every candidate match, grouped by the observed URL (one real request maps
    #    to one endpoint — so a URL contested by several findings is resolved per-URL).
    by_request: dict[str, list[_Candidate]] = {}
    for req in requests:
        method = req.get("method") or ""
        url = req.get("url") or ""
        obs = _segments(urlsplit(url).path)
        for ep in endpoints:
            if ep.method != method:
                continue
            parsed = _parse_tail(ep.path)
            if parsed is None:
                continue
            tail, host_templated = parsed
            absorbed = _absorbed(tail, obs, host_templated)
            if absorbed is None:
                continue
            const_count = sum(1 for s in tail if not _is_param(s))
            by_request.setdefault(url, []).append(
                _Candidate(ep.finding_hash, url, const_count, absorbed)
            )

    # 2. Per observed URL, the most-specific finding wins (most constants, least base
    #    absorbed); a tie between findings is ambiguous and dropped (honesty).
    per_finding: dict[str, set[str]] = {}
    for cands in by_request.values():
        best = max(cands, key=_specificity)
        winners = [c for c in cands if _specificity(c) == _specificity(best)]
        if len(winners) != 1:
            continue  # contested URL with no unique most-specific finding -> skip
        winner = winners[0]
        per_finding.setdefault(winner.finding_hash, set()).add(winner.resolved_url)

    # 3. A finding resolves only if it matched exactly one distinct URL; two different
    #    hosts/paths mean an inconsistent observation -> skip, don't guess.
    return {fh: next(iter(urls)) for fh, urls in per_finding.items() if len(urls) == 1}
