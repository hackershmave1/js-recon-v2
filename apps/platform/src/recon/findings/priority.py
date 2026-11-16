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
    # Client-side data-flow sinks (D52): informational, not exploitable by themselves.
    # postMessage listener (XSS-via-message attack surface) outranks a storage write
    # because it is the injection point; storage writes are lower-signal alone.
    "postmessage_sink": 35,
    "storage_sink": 25,
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


def _risk_tags(attributes: Mapping[str, Any] | None) -> tuple[str, ...]:
    raw = (attributes or {}).get("risk_tags")
    return tuple(str(t) for t in raw) if isinstance(raw, (list, tuple)) else ()


def explain_priority(
    finding_type: str, risk_tags: tuple[str, ...] | list[str] = ()
) -> dict[str, Any]:
    """Why a finding scores what it does: the type's base, the one risk tag that bumped it.

    Same inputs as ``priority_score`` so the explanation can never disagree with the score.
    ``risk_tag`` is the single highest-bump tag (the one that counted), or None.
    """
    score = priority_score(finding_type, risk_tags)
    tags = [t for t in risk_tags if t in _TAG_BUMP]
    winner = max(tags, key=lambda t: _TAG_BUMP[t], default=None)
    return {
        "base": _TYPE_BASE.get(finding_type, _DEFAULT_BASE),
        "risk_tag": winner,
        "bump": _TAG_BUMP[winner] if winner else 0,
        "score": score,
        "label": priority_label(score),
    }


def derive_priority(finding_type: str, attributes: Mapping[str, Any] | None) -> tuple[int, str]:
    """Return ``(score, label)`` for a finding, reading ``risk_tags`` from ``attributes``."""
    score = priority_score(finding_type, _risk_tags(attributes))
    return score, priority_label(score)


def derive_priority_reason(
    finding_type: str, attributes: Mapping[str, Any] | None
) -> dict[str, Any]:
    """``explain_priority`` reading ``risk_tags`` from a finding's ``attributes``."""
    return explain_priority(finding_type, _risk_tags(attributes))
