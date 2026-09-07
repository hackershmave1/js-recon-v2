"""Client-side data-flow sink detector — postMessage listeners and Web Storage / cookie writes.

Finds two families of client-side sink:

``POSTMESSAGE_SINK``
    An ``addEventListener('message', handler)`` call site — the attack surface for
    XSS-via-postMessage. A listener whose origin-check is absent or weak can accept attacker
    messages. Detected by the pattern ``addEventlistener\\s*\\(\\s*['"]message['"]``.

``STORAGE_SINK``
    A ``localStorage`` / ``sessionStorage`` write (``setItem`` / ``removeItem`` / ``clear``) or
    a ``document.cookie`` assignment — sites where user-controlled data is persisted client-side.
    Detected by ``localStorage|sessionStorage .setItem/removeItem/clear`` OR
    ``document.cookie =``.

Both are INFO-disclosure / attack-surface family (NOT secrets, NOT endpoints):
- Values are stored CLEARTEXT (never sha256-hashed, never redacted, never reveal-gated).
- Counted SEPARATELY from secrets and endpoints (no REQ-C2 coverage counter movement).
- Excluded from every ``type == 'endpoint'`` read model automatically (DISTINCT types).
- Outside the REQ-D5 removal diff (scoped to ``secret`` + confirmed endpoint lanes).

This module is PURE: no DB, no I/O, no engine subprocess — just ``str`` in, a list of
``DataSinkSighting`` out — so it is hermetically unit-testable in the fast lane.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Maximum snippet length stored as the finding value (long call-site text trimmed).
_MAX_VALUE_LEN = 120

# Default per-blob cap per sink type — a blob dense with storage calls must not blow up emit.
_DEFAULT_CAP = 500

# postMessage listener: addEventListener('message', …) — boundary-guarded so `onMessage` or
# `addOneEventListener` are not caught. The method name is matched case-insensitively (rare
# minifier artefacts may vary case); the literal 'message' must be lowercase since the browser
# event type is case-sensitive and minifiers never alter string literal content.
_POSTMESSAGE_RE = re.compile(
    r"\baddEventListener\s*\(\s*['\"]message['\"]",
    re.IGNORECASE,
)

# Web Storage writes: setItem / removeItem / clear on localStorage or sessionStorage.
# document.cookie assignment: `document.cookie =` (with optional whitespace).
_STORAGE_RE = re.compile(
    r"\b(localStorage|sessionStorage)\s*\.\s*(setItem|removeItem|clear)\s*\("
    r"|document\s*\.\s*cookie\s*="
)


@dataclass(frozen=True)
class DataSinkSighting:
    """One data-sink call site located in a source unit.

    ``sink_type`` is ``'postmessage_sink'`` or ``'storage_sink'`` — the ``FindingType`` value.
    ``value`` is the matched expression trimmed to ``_MAX_VALUE_LEN`` chars (stored CLEARTEXT
    as the finding value). ``evidence`` is the full source line containing the match (for the
    occurrence evidence field). ``line`` is the 1-based line number of the match start.
    ``offset_start``/``offset_end`` are char offsets into the decoded source string.
    """

    sink_type: str
    value: str
    evidence: str
    line: int
    offset_start: int
    offset_end: int


def _extract_line(text: str, offset: int) -> str:
    """Return the full source line that contains ``offset`` (no trailing newline)."""
    line_start = text.rfind("\n", 0, offset)
    line_start = 0 if line_start == -1 else line_start + 1
    line_end = text.find("\n", offset)
    line_end = len(text) if line_end == -1 else line_end
    return text[line_start:line_end]


def find_data_sinks(text: str, *, cap: int = _DEFAULT_CAP) -> list[DataSinkSighting]:
    """Return every data-sink call site in ``text``, in source order.

    Stops after ``cap`` sightings PER SINK TYPE so a blob dense with storage calls can't
    blow up emit. Sightings from both sink types are interleaved in source order.

    Each sighting's ``value`` is the matched source text trimmed to ``_MAX_VALUE_LEN`` chars
    so the finding value is always a compact, readable snippet. ``evidence`` is the full
    containing source line (for the occurrence evidence field).
    """
    sightings: list[DataSinkSighting] = []
    postmessage_count = 0
    storage_count = 0

    # Collect all matches from both patterns, tagged by type, then sort by offset.
    candidates: list[tuple[int, re.Match[str], str]] = []
    for match in _POSTMESSAGE_RE.finditer(text):
        candidates.append((match.start(), match, "postmessage_sink"))
    for match in _STORAGE_RE.finditer(text):
        candidates.append((match.start(), match, "storage_sink"))
    candidates.sort(key=lambda t: t[0])

    for offset_start, match, sink_type in candidates:
        if sink_type == "postmessage_sink":
            if postmessage_count >= cap:
                continue
            postmessage_count += 1
        else:
            if storage_count >= cap:
                continue
            storage_count += 1

        matched_text = match.group(0)
        value = matched_text[:_MAX_VALUE_LEN]
        evidence = _extract_line(text, offset_start)
        line = text.count("\n", 0, offset_start) + 1
        sightings.append(
            DataSinkSighting(
                sink_type=sink_type,
                value=value,
                evidence=evidence,
                line=line,
                offset_start=offset_start,
                offset_end=match.end(),
            )
        )

    return sightings
