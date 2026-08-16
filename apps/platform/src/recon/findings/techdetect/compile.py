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

from recon.observability import get_logger

if TYPE_CHECKING:
    # NOTE: dataset.py doesn't exist yet (Task 4 delivers it) — this forward
    # reference is only ever resolved for compile_all()'s annotation below, which
    # Task 4 also implements. Drop the ignore once dataset.py lands; mypy --strict's
    # warn-unused-ignores will then flag it dead so it can't be forgotten.
    from recon.findings.techdetect import dataset  # type: ignore[attr-defined]

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
    """Compile every fingerprint field of every technology, skipping (and counting)
    RE2-rejected patterns. Placeholder in Task 3 — implemented in Task 4 once
    ``dataset.RawTechnology`` exists."""
    raise NotImplementedError  # Task 4
