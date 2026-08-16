"""A typed adapter over ``google-re2`` + a defensive per-pattern compiler.

``google-re2`` ships no type stubs, so ``import re2`` resolves to ``Any`` under
``ignore_missing_imports``. This module wraps it behind ``Protocol`` types and one
``cast`` so the rest of ``recon.findings.techdetect`` stays mypy-strict (T8). Every
pattern is compiled through ``try_compile``: RE2 rejects some enthec constructs
(lookbehind, backreferences) at compile time, and a reject must be SKIPPED + counted,
never fatal to the whole dataset (T4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import re2  # google-re2 — untyped; wrapped behind the Protocols below

from recon.findings.techdetect import version
from recon.observability import get_logger

if TYPE_CHECKING:
    # Only used for compile_all()'s annotation below; `from __future__ import
    # annotations` means this never needs to be a real (runtime) import.
    from recon.findings.techdetect import dataset

log = get_logger("recon.findings.techdetect.compile")


class Re2Match(Protocol):
    def group(self, index: int = 0, /) -> str: ...
    def groups(self) -> tuple[str | None, ...]: ...


class Re2Pattern(Protocol):
    def search(self, text: str, /) -> Re2Match | None: ...


def compile_pattern(source: str, *, case_insensitive: bool = True) -> Re2Pattern:
    """Compile one pattern under RE2, case-insensitive by default. May raise
    ``re2.error`` on a pattern RE2 rejects — callers use ``try_compile`` instead."""
    options = re2.Options()
    options.case_sensitive = not case_insensitive
    # RE2's own C++ layer writes "Error parsing '<pattern>'" straight to the
    # process's native stderr on a parse failure (bypassing structlog) unless told
    # not to — this is RE2::Options' own `log_errors` knob (default True) guarding
    # exactly that call, so it's the correct place to silence it. try_compile()
    # below already emits a structured log.debug for every skip, so nothing about
    # the reject goes unrecorded — only the native, unstructured stderr line dies.
    options.log_errors = False
    return cast("Re2Pattern", re2.compile(source, options=options))


def try_compile(source: str) -> Re2Pattern | None:
    """Compile ``source`` or return ``None`` if RE2 rejects it (lookbehind /
    backreference / bad syntax). The reject is logged at debug and counted by the
    caller — the dataset load is never all-or-nothing (T4)."""
    try:
        return compile_pattern(source)
    except re2.error as exc:  # google-re2 raises re2.error (drop-in for re.error)
        log.debug("techdetect.pattern_skipped", source=source, error=str(exc))
        return None


@dataclass(frozen=True)
class CompiledPattern:
    """One compiled fingerprint pattern bound to the signal surface it matches."""

    surface: str  # "headers" | "cookies" | "scriptSrc" | "scripts" | "meta"
    key: str | None  # header/cookie/meta NAME (lowercased); None for scriptSrc/scripts
    regex: Re2Pattern
    version_template: str | None
    confidence: int


@dataclass(frozen=True)
class CompiledTech:
    name: str
    categories: tuple[int, ...]
    patterns: tuple[CompiledPattern, ...]


def compile_all(
    raw_techs: dict[str, dataset.RawTechnology],
) -> tuple[list[CompiledTech], int]:
    """Compile every fingerprint field of every technology into CompiledPatterns,
    skipping (and counting) RE2-rejected patterns (T4). Only the Phase-1 surfaces are
    compiled — headers, cookies, scriptSrc, scripts, meta; ``js``/``html`` are Phase 2."""
    compiled: list[CompiledTech] = []
    skipped = 0
    for name, tech in raw_techs.items():
        patterns: list[CompiledPattern] = []
        skipped += _compile_mapping(patterns, "headers", tech.get("headers"))
        skipped += _compile_mapping(patterns, "cookies", tech.get("cookies"))
        skipped += _compile_mapping(patterns, "meta", tech.get("meta"))
        skipped += _compile_list(patterns, "scriptSrc", tech.get("scriptSrc"))
        skipped += _compile_list(patterns, "scripts", tech.get("scripts"))
        compiled.append(
            CompiledTech(
                name=name,
                categories=tuple(tech.get("cats", [])),
                patterns=tuple(patterns),
            )
        )
    return compiled, skipped


def _compile_mapping(
    out: list[CompiledPattern], surface: str, mapping: dict[str, str] | None
) -> int:
    """Compile a name->pattern mapping (headers/cookies/meta). Returns the skip count."""
    if not mapping:
        return 0
    skipped = 0
    for key, raw in mapping.items():
        regex_source, tags = version.parse_field_value(raw or "")
        regex = try_compile(regex_source or "")  # "" (cookie presence) compiles to match-all
        if regex is None:
            skipped += 1
            continue
        out.append(
            CompiledPattern(
                surface=surface,
                key=key.lower(),
                regex=regex,
                version_template=tags.version,
                confidence=tags.confidence,
            )
        )
    return skipped


def _compile_list(out: list[CompiledPattern], surface: str, values: list[str] | None) -> int:
    """Compile a list of patterns (scriptSrc/scripts). Returns the skip count."""
    if not values:
        return 0
    skipped = 0
    for raw in values:
        regex_source, tags = version.parse_field_value(raw)
        regex = try_compile(regex_source)
        if regex is None:
            skipped += 1
            continue
        out.append(
            CompiledPattern(
                surface=surface,
                key=None,
                regex=regex,
                version_template=tags.version,
                confidence=tags.confidence,
            )
        )
    return skipped
