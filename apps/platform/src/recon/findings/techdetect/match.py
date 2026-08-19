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
from recon.findings.techdetect.compile import CompiledPattern, CompiledTech, JsSurface, Re2Match

_EVIDENCE_MAX = 200  # a bounded marker snippet - never a full body (T1)
_SCRIPTS_ZERO_WIDTH_PLACEHOLDER = "<scripts match>"  # never the raw JS body (T1)
# The js (window-global) surface is matched against static bundle SOURCE, not a live
# runtime, so a presence hit is weaker evidence than enthec's runtime-calibrated
# confidences imply. Cap each tech's TOTAL js contribution here so a js-only detection
# reads as "suspected" and can never reach a "certain" 100 on static source alone -
# only Phase-1 corroboration (headers/scripts/...) crosses the ceiling.
_JS_SURFACE_CEILING = 50


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
    js_confidence: int = 0  # js-surface contribution, capped separately (see match())
    version: str | None = None
    version_confidence: int = -1  # the individual pattern-confidence behind `version`
    evidence: list[str] = field(default_factory=list)


def match(
    compiled: list[CompiledTech],
    categories: dict[str, str],
    host: str,
    signal: dict[str, Any],
    js_texts: list[str],
    js_surface: JsSurface,
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
    _match_js_surface(accs, js_surface, js_texts)
    return [
        Detection(
            name=name,
            categories=category_names(list(acc.categories), categories),
            version=acc.version,
            # The js contribution is capped BEFORE summing with Phase-1, so a js-only
            # tech tops out at the ceiling ("suspected") and only real Phase-1 evidence
            # can push a technology to a "certain" 100.
            confidence=min(acc.confidence + min(acc.js_confidence, _JS_SURFACE_CEILING), 100),
            evidence=acc.evidence,
        )
        for name, acc in sorted(accs.items())
    ]


def _match_js_surface(
    accs: dict[str, _Accumulator], js_surface: JsSurface, js_texts: list[str]
) -> None:
    """Fold the presence-only ``js`` runtime-global surface into the per-tech tally.

    One RE2 ``Set`` pass per stored JS text; ``Set.Match`` returns the matched indices,
    or ``None`` on the (common) no-match — guard it. A global name is ONE signal however
    many assets carry it, so dedup by Set index. Its confidence lands in the separate
    ``js_confidence`` bucket the caller caps (OBJ-2). Evidence is the enthec key literal
    only — ``Set.Match`` yields no offsets, so unlike ``scripts`` no neighbouring source
    byte can leak into evidence (T1)."""
    if not js_texts:
        return
    seen: set[int] = set()
    for text in js_texts:
        for index in js_surface.matcher.Match(text) or ():
            if index in seen:
                continue
            seen.add(index)
            js_pattern = js_surface.patterns[index]
            acc = accs.setdefault(js_pattern.tech, _Accumulator(categories=js_pattern.categories))
            acc.js_confidence += js_pattern.confidence
            acc.evidence.append(_bounded(f"js: {js_pattern.key}"))


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
        # The signal only carries meta tag *content*, not tag names, so we can't tell a
        # "generator" meta from any other. Phase 1 therefore supports only "generator"
        # patterns; the full dataset's non-generator meta patterns are compiled but
        # never matched here — a known Phase-1 coverage gap, not a correctness bug.
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
