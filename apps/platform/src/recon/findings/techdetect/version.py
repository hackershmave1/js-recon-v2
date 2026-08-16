"""Parse the enthec/webappanalyzer field-value grammar (Wappalyzer-compatible).

A dataset field value is ``<regex>[\\;version:<template>][\\;confidence:<n>]`` where
``\\;`` is a LITERAL two-char separator (not a regex escape). The version template
substitutes capture groups (``\\1``..``\\9``) and supports one ternary
``\\1?present:absent``. Only OUR tiny, trusted grammar is parsed with stdlib ``re``;
the untrusted dataset regex itself is compiled with ``google-re2`` (see compile.py).
"""

from __future__ import annotations

import re as _stdlib_re
from dataclasses import dataclass

# \1?a:b — a ternary keyed on whether capture group N matched. Non-greedy present
# branch up to the first ':'; absent branch is the remainder.
_TERNARY = _stdlib_re.compile(r"\\(\d)\?([^:]+):(.*)$")
# A bare \N group reference.
_GROUP = _stdlib_re.compile(r"\\(\d)")

_DEFAULT_CONFIDENCE = 100  # enthec default when a field carries no confidence tag


@dataclass(frozen=True)
class PatternTags:
    version: str | None
    confidence: int


def parse_field_value(raw: str) -> tuple[str, PatternTags]:
    """Split an enthec field value into its regex source and its tags."""
    parts = raw.split("\\;")
    regex = parts[0]
    version: str | None = None
    confidence = _DEFAULT_CONFIDENCE
    for tag in parts[1:]:
        key, _, value = tag.partition(":")
        if key == "version":
            version = value
        elif key == "confidence":
            try:
                confidence = int(value)
            except ValueError:
                confidence = _DEFAULT_CONFIDENCE
    return regex, PatternTags(version=version, confidence=confidence)


def resolve_version(template: str | None, groups: tuple[str | None, ...]) -> str | None:
    """Resolve a version template against a match's capture groups, or ``None``.

    ``\\1``..``\\9`` substitute the group (1-indexed; ``\\1`` -> ``groups[0]``); an
    absent group substitutes ``""``. A single ternary ``\\N?a:b`` chooses ``a`` when
    group N matched, else ``b``. An empty result becomes ``None`` (never store "")."""
    if not template:
        return None
    resolved = template
    ternary = _TERNARY.search(resolved)
    if ternary is not None:
        index = int(ternary.group(1))
        chosen = ternary.group(2) if _group(groups, index) else ternary.group(3)
        resolved = resolved.replace(ternary.group(0), chosen)
    for digit in _GROUP.findall(resolved):
        resolved = resolved.replace(f"\\{digit}", _group(groups, int(digit)) or "")
    resolved = resolved.strip()
    return resolved or None


def _group(groups: tuple[str | None, ...], index: int) -> str | None:
    """The 1-indexed capture group, or ``None`` if out of range / unmatched."""
    return groups[index - 1] if 1 <= index <= len(groups) else None
