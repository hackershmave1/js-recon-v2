"""Deterministic, explainable finding priority (D49).

Nothing ever populated the ``severity`` column, so on a 500+/2000-asset run an operator
had no signal for what to look at first. This derives a priority at READ time (no
migration, applies to every existing run, reversible) from two signals already on each
finding: its TYPE (a leaked secret outranks a suspected endpoint) and its param RISK
TAGS (``auth``/``admin``/``idor``/``flag`` — the one real risk signal the pipeline
computes, ``findings.risk_tags``). Pure + stdlib so it is trivially unit-testable and
identical across every consumer.

Deliberately simple/explainable: type base + the single highest-risk tag bump. Richer
inputs the review named (shadow/documented status, unattributed) are intentionally left
for a follow-up rather than baked into an opaque score.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Base weight by finding type. A confirmed leaked secret tops the list; the
# suspected/low-confidence lanes sit below their confirmed siblings; a page route is
# lowest (client navigation, not backend surface). Unknown types fall back to 20.
_TYPE_BASE: dict[str, int] = {
    "secret": 90,
    "internal_ip": 60,
    "secret_suspected": 55,
    "graphql": 45,
    "endpoint": 45,
    "param": 30,
    "endpoint_suspected": 30,
    "endpoint_unresolved": 25,
    "endpoint_generic": 20,
    "page_route": 15,
}
_DEFAULT_BASE = 20

# Additive bump for the HIGHEST-risk param tag on the finding (max, not sum, so a
# multi-tagged param can't run away past a real secret). admin/idor are access-control
# smells worth poking first; auth marks a credential-bearing param; flag is lower.
_TAG_BUMP: dict[str, int] = {"admin": 25, "idor": 20, "auth": 15, "flag": 5}

# (label, inclusive floor), highest first.
_LABELS: tuple[tuple[str, int], ...] = (
    ("critical", 80),
    ("high", 50),
    ("medium", 30),
    ("low", 0),
)


def priority_score(finding_type: str, risk_tags: tuple[str, ...] | list[str] = ()) -> int:
    base = _TYPE_BASE.get(finding_type, _DEFAULT_BASE)
    bump = max((_TAG_BUMP.get(t, 0) for t in risk_tags), default=0)
    return min(100, base + bump)


def priority_label(score: int) -> str:
    for label, floor in _LABELS:
        if score >= floor:
            return label
    return "low"


def derive_priority(finding_type: str, attributes: Mapping[str, Any] | None) -> tuple[int, str]:
    """Return ``(score, label)`` for a finding, reading ``risk_tags`` from ``attributes``."""
    tags: tuple[str, ...] = ()
    if attributes:
        raw = attributes.get("risk_tags")
        if isinstance(raw, (list, tuple)):
            tags = tuple(str(t) for t in raw)
    score = priority_score(finding_type, tags)
    return score, priority_label(score)
