"""The cleartext internal-IP detector — an info-disclosure signal, NOT a secret.

Finds IPv4 literals from the private/loopback/link-local ranges embedded in a JS
bundle (or a source-map-recovered original). These are info-disclosure: a hardcoded
``10.x`` / ``127.x`` / ``169.254.x`` address leaks internal network topology. The
value is plainly visible in the source, so — unlike a SECRET — it is stored and shown
in CLEARTEXT (never sha256-hashed into identity, never redacted, never reveal-gated)
and counted separately from secrets.

This module is PURE: no DB, no I/O, no engine subprocess — just ``str`` in, a list of
``InternalIpSighting`` out — so it is hermetically unit-testable in the fast lane.

Detection is IPv4-only and range-locked (no hostnames, no IPv6). We deliberately do
NOT use ``ipaddress.is_private`` — it over-matches (e.g. it treats the whole
``172.16.0.0/12`` block and reserved ranges we don't want, and it can't reject a quad
embedded in a longer token). Instead we scan with a boundary-guarded ``re`` pattern
(stdlib ``re``, linear, no backtracking risk) and classify by the explicit ranges:

- ``10.0.0.0/8``                         -> ``rfc1918``
- ``172.16.0.0/12`` (172.16 - 172.31)    -> ``rfc1918``
- ``192.168.0.0/16``                     -> ``rfc1918``
- ``127.0.0.0/8``                        -> ``loopback``
- ``169.254.0.0/16``                     -> ``link-local``

Anything else (public 8.8.8.8, out-of-range 172.15.x / 172.32.x, an octet > 255) is
skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Default per-blob sighting cap. A data blob full of dotted-quads (an IP allow-list, a
# geo table) must not blow up emit, so we stop after this many sightings.
_DEFAULT_CAP = 5000

# Boundary-guarded dotted-quad. The negative lookbehind/lookahead reject a quad embedded
# in a longer alnum/dotted token — ``v1.2.3.4``, ``x10.0.0.1`` — and force the LONGEST
# valid last octet, so ``192.168.1.10`` yields ``.10`` (not ``.1``) and the trailing-dot
# ``192.168.1.1.5`` yields NO match (the ``.`` after the fourth octet trips the lookahead,
# and every shifted start is preceded by ``[\w.]`` so it never mis-slices ``192.168.1.1``).
_DOTTED_QUAD = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")


@dataclass(frozen=True)
class InternalIpSighting:
    """One cleartext internal-IP literal located in a source unit.

    ``value`` is the RAW dotted-quad exactly as it appears (e.g. ``"10.0.0.1"``) — it is
    stored cleartext as the finding value, never hashed. ``offset_start``/``offset_end``
    are char offsets into the decoded ``source`` string (``match.span()``), the same byte
    space secrets use. ``category`` is one of ``rfc1918`` / ``loopback`` / ``link-local``.
    """

    value: str
    offset_start: int
    offset_end: int
    line: int
    category: str


def _classify(octet1: int, octet2: int, octet3: int, octet4: int) -> str | None:
    """Map four in-range (0-255) octets to their internal-IP category, or ``None`` when
    the address is not one of the five locked private/loopback/link-local ranges.

    Only the leading octet(s) decide the category — ``octet3``/``octet4`` are already
    known valid (<= 255) by the caller, so they never affect classification, but taking
    all four keeps the signature self-documenting as "classify a full address"."""
    if octet1 == 10:
        return "rfc1918"  # 10.0.0.0/8
    if octet1 == 172 and 16 <= octet2 <= 31:
        return "rfc1918"  # 172.16.0.0/12
    if octet1 == 192 and octet2 == 168:
        return "rfc1918"  # 192.168.0.0/16
    if octet1 == 127:
        return "loopback"  # 127.0.0.0/8
    if octet1 == 169 and octet2 == 254:
        return "link-local"  # 169.254.0.0/16
    return None


def find_internal_ips(text: str, *, cap: int = _DEFAULT_CAP) -> list[InternalIpSighting]:
    """Return every locked-range internal-IP literal in ``text``, in source order.

    Stops after ``cap`` sightings so a blob dense with dotted-quads can't blow up emit.
    Each candidate is a boundary-guarded dotted-quad; a candidate whose any octet exceeds
    255, or whose address falls outside the five locked ranges, is skipped.
    """
    sightings: list[InternalIpSighting] = []
    for match in _DOTTED_QUAD.finditer(text):
        octet1, octet2, octet3, octet4 = (int(group) for group in match.groups())
        if octet1 > 255 or octet2 > 255 or octet3 > 255 or octet4 > 255:
            continue  # a bare \d{1,3} can match 256-999; those are not valid octets
        category = _classify(octet1, octet2, octet3, octet4)
        if category is None:
            continue  # public or out-of-range — not an internal-IP disclosure
        offset_start, offset_end = match.span()
        sightings.append(
            InternalIpSighting(
                value=match.group(0),
                offset_start=offset_start,
                offset_end=offset_end,
                line=text.count("\n", 0, offset_start) + 1,
                category=category,
            )
        )
        if len(sightings) >= cap:
            break
    return sightings
