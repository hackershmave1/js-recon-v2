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


class Re2Set(Protocol):
    """The subset of ``google-re2``'s ``Set`` we use: add N patterns, compile once,
    then one linear pass returns the indices that matched (or ``None`` on no match)."""

    def Add(self, pattern: str, /) -> int: ...
    # google-re2's wrapper returns None and RAISES on a compile failure, so a Set that
    # overflows RE2's budget on a future dataset re-pin fails CLOSED at load time.
    def Compile(self) -> None: ...
    def Match(self, text: str, /) -> list[int] | None: ...


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


@dataclass(frozen=True)
class JsPattern:
    """One presence-matchable enthec ``js`` global name, index-aligned with its slot in
    a compiled ``JsSurface`` Set. Presence-only: static bundle source can't read the
    global's RUNTIME value, so there is no version template - only the tech it maps to
    and the confidence its presence contributes."""

    tech: str
    key: str
    categories: tuple[int, ...]
    confidence: int


@dataclass(frozen=True)
class JsSurface:
    """The compiled ``js`` (window-global) surface: one RE2 ``Set`` of every DISTINCTIVE
    global name plus an index-aligned lookup back to the technology each pattern came
    from. Matched in one linear pass per bundle (``matcher.Match`` -> indices)."""

    matcher: Re2Set
    patterns: tuple[JsPattern, ...]


def compile_all(
    raw_techs: dict[str, dataset.RawTechnology],
) -> tuple[list[CompiledTech], int]:
    """Compile every fingerprint field of every technology into CompiledPatterns,
    skipping (and counting) RE2-rejected patterns (T4). Compiles the per-pattern
    surfaces — headers, cookies, scriptSrc, scripts, meta; the ``js`` (window-global)
    surface compiles into a single Set via :func:`compile_js_surface` instead (one
    linear pass beats looping thousands of patterns), and ``html``/``dom`` stay
    unimplemented (they need raw HTML / rendered DOM the allowlist signal omits)."""
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


_JS_MIN_KEY_LEN = 4  # anything shorter collides constantly as a token in minified source
_JS_BARE_ALPHA_MIN_LEN = 8  # a bare [A-Za-z] word (no sigil) must be at least this long


def _is_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")


def _keep_js_key(key: str) -> bool:
    """Whether an enthec ``js`` global name is DISTINCTIVE enough to presence-match in
    minified bundle source without systematic false positives.

    Two drops (both design exclusions — the key never enters the Set — NOT T4 RE2
    rejects): (1) anything under 4 chars (``va``, ``Vue``, ``Ext`` fire on incidental
    tokens); (2) a bare ASCII word with no ``_``/``$``/``.`` sigil under 8 chars
    (``core``, ``Chart``, ``Alpine`` — ordinary identifiers/English words that a bundle
    ships or mentions in passing). Distinctive markers (``__NEXT_DATA__``, ``$nuxt``,
    ``React.version``, long names) all pass; measured against the vendored dataset this
    keeps 5020/5642 keys and only strips the false-positive band."""
    if len(key) < _JS_MIN_KEY_LEN:
        return False
    # A bare ASCII word (no _/$/. sigil) under 8 chars is an ordinary identifier.
    return not (key.isascii() and key.isalpha() and len(key) < _JS_BARE_ALPHA_MIN_LEN)


def _js_word_bounded(key: str) -> str:
    """An RE2 source that presence-matches a js global name at a token boundary. ``\\b``
    is added only on an end that is itself a word char — RE2 has no lookaround, and a
    ``\\b`` against ``$nuxt``'s leading ``$`` or ``.version``'s leading ``.`` would
    mis-anchor. The name itself is ``re2.escape``d so ``.`` stays literal."""
    source = cast("str", re2.escape(key))
    if _is_word_char(key[0]):
        source = r"\b" + source
    if _is_word_char(key[-1]):
        source = source + r"\b"
    return source


def compile_js_surface(
    raw_techs: dict[str, dataset.RawTechnology],
) -> tuple[JsSurface, int]:
    """Compile every DISTINCTIVE enthec ``js`` global name (see :func:`_keep_js_key`)
    into ONE RE2 ``Set`` for a single linear presence-pass over bundle source.

    Looping the ~5.6k js patterns individually measured ~50s over a 2 MB blob (past the
    job lease); the Set matches the same blob in ~0.01s. The Set gets its OWN Options:
    ``case_sensitive`` (js identifiers are case-sensitive — matching insensitively
    inflates false positives) and ``log_errors`` off (RE2 writes parse failures to
    native stderr otherwise, mirroring :func:`compile_pattern`). Every ``Add`` is
    guarded so one RE2-rejected pattern skips + counts rather than aborting the load
    (T4); the list is appended only on a successful ``Add`` so ``patterns[i]`` stays
    aligned with Set index ``i`` (``Add`` returns sequential indices). Returns
    ``(surface, RE2-reject-count)`` — deliberate non-distinctive drops are NOT counted."""
    options = re2.Options()
    options.case_sensitive = True
    options.log_errors = False
    matcher = cast("Re2Set", re2.Set.SearchSet(options))
    patterns: list[JsPattern] = []
    skipped = 0
    for name, tech in raw_techs.items():
        js = tech.get("js")
        if not js:
            continue
        categories = tuple(tech.get("cats", []))
        for key, raw in js.items():
            if not _keep_js_key(key):
                continue
            # Only the enthec confidence tag matters here; the value regex targets the
            # global's RUNTIME value, which static source can't supply, so it is dropped.
            _regex, tags = version.parse_field_value(raw or "")
            try:
                matcher.Add(_js_word_bounded(key))
            except re2.error:
                skipped += 1
                log.debug("techdetect.js_pattern_skipped", key=key)
                continue
            patterns.append(
                JsPattern(tech=name, key=key, categories=categories, confidence=tags.confidence)
            )
    matcher.Compile()
    return JsSurface(matcher=matcher, patterns=tuple(patterns)), skipped
