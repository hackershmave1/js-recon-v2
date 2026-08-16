"""Apply compiled fingerprint patterns to one host's signal + JS. Pure.

Confidence is SUMMED across every matching pattern of a technology and capped at 100
(enthec confidences are designed to combine toward 100 - T3). On conflicting versions
the highest-INDIVIDUAL-confidence pattern's version wins regardless of match order;
every other, differing version is still kept in ``evidence`` as an alternate rather
than silently dropped. ``implies``/``requires``/``excludes`` are NOT followed in
Phase 1 (flat list)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from recon.findings.techdetect import version as version_mod
from recon.findings.techdetect.compile import CompiledPattern, CompiledTech, Re2Match

_EVIDENCE_MAX = 200  # a bounded marker snippet - never a full body (T1)
_SCRIPTS_ZERO_WIDTH_PLACEHOLDER = "<scripts match>"  # never the raw JS body (T1)


@dataclass(frozen=True)
class Detection:
    name: str
    categories: list[str]
    version: str | None
    confidence: int
    evidence: list[str]


@dataclass
class _Accumulator:
    """Running per-technology tally while scanning every matching pattern."""

    categories: tuple[int, ...] = ()
    confidence: int = 0
    version: str | None = None
    version_confidence: int = -1  # the individual pattern-confidence behind `version`
    evidence: list[str] = field(default_factory=list)


def match(
    compiled: list[CompiledTech],
    categories: dict[str, str],
    host: str,
    signal: dict[str, Any],
    js_texts: list[str],
) -> list[Detection]:
    from recon.findings.techdetect.dataset import category_names

    accs: dict[str, _Accumulator] = {}
    for tech in compiled:
        for pattern in tech.patterns:
            for value in _surface_values(pattern, signal, js_texts):
                found = pattern.regex.search(value)
                if found is None:
                    continue
                acc = accs.setdefault(tech.name, _Accumulator(categories=tech.categories))
                _record(acc, pattern, found, value)
    return [
        Detection(
            name=name,
            categories=category_names(list(acc.categories), categories),
            version=acc.version,
            confidence=min(acc.confidence, 100),
            evidence=acc.evidence,
        )
        for name, acc in sorted(accs.items())
    ]


def _surface_values(
    pattern: CompiledPattern, signal: dict[str, Any], js_texts: list[str]
) -> list[str]:
    """The candidate strings a pattern is searched against, per its surface."""
    if pattern.surface == "headers":
        value = signal.get("headers", {}).get(pattern.key)
        return [value] if value else []
    if pattern.surface == "cookies":
        # enthec cookie patterns test presence of the NAME (value regex usually empty).
        return [pattern.key or ""] if (pattern.key in signal.get("cookies", [])) else []
    if pattern.surface == "scriptSrc":
        # scriptSrc matches script URLs; the signal's own "scripts" field IS that URL
        # list (js_texts, a separate parameter, holds JS *source* for the "scripts"
        # dataset surface below - the two "scripts" names refer to different things).
        return list(signal.get("scripts", []))
    if pattern.surface == "meta":
        # The signal only carries meta tag *content*, not tag names, so we can't tell
        # a "generator" meta from any other; every enthec meta pattern in the vendored
        # dataset happens to key "generator", so that's the only one Phase 1 supports.
        return list(signal.get("meta", [])) if pattern.key == "generator" else []
    if pattern.surface == "scripts":
        return js_texts
    return []


def _record(acc: _Accumulator, pattern: CompiledPattern, found: Re2Match, value: str) -> None:
    """Fold one matching pattern into its technology's accumulator: sum confidence
    (the caller caps the total at 100), and keep the highest-confidence version -
    any other, differing version is filed under `evidence` as an alternate instead
    of being discarded, however it ranks against the patterns matched so far (T3)."""
    acc.confidence += pattern.confidence
    resolved = version_mod.resolve_version(pattern.version_template, found.groups())
    if resolved is not None and resolved != acc.version:
        if pattern.confidence > acc.version_confidence:
            if acc.version is not None:
                acc.evidence.append(_bounded(f"version alt: {acc.version}"))
            acc.version = resolved
            acc.version_confidence = pattern.confidence
        else:
            acc.evidence.append(_bounded(f"version alt: {resolved}"))
    # Bound the snippet to the actual match (Re2Match.group(0)), never the full raw
    # surface value - `value` can be an entire JS file for the "scripts" surface.
    # A presence-only pattern (empty regex, e.g. a cookie name or Cloudflare's
    # cf-ray) matches a zero-width string, so fall back to the raw value there -
    # EXCEPT on the "scripts" surface, where `value` IS an entire JS file and could
    # itself carry a secret (T1): a zero-width match there gets a fixed placeholder,
    # never the raw source.
    if found.group(0):
        snippet = found.group(0)
    elif pattern.surface == "scripts":
        snippet = _SCRIPTS_ZERO_WIDTH_PLACEHOLDER
    else:
        snippet = value
    marker = f"{pattern.key or pattern.surface}: {snippet}"
    acc.evidence.append(_bounded(marker))


def _bounded(text: str) -> str:
    return text[:_EVIDENCE_MAX]
