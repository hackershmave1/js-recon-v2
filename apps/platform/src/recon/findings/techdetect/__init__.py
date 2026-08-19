"""In-house pure-Python technology fingerprinter over the vendored enthec dataset.

``detect(host, signal, js_texts)`` matches the dataset's per-pattern surfaces (response
headers, cookie names, script URLs, ``<meta generator>``, and JS source via the
``scripts`` field) plus the ``js`` window-global surface (global names presence-matched
in bundle source through one RE2 ``Set``) with ``google-re2`` (ReDoS-safe). No network,
no secret storage: input is only the allowlisted fingerprint-signal + already-stored JS
bytes."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from recon.findings.techdetect import compile as _compile
from recon.findings.techdetect import dataset as _dataset
from recon.findings.techdetect import match as _match
from recon.findings.techdetect.compile import CompiledTech
from recon.findings.techdetect.match import Detection

__all__ = ["Detection", "dataset_commit", "detect", "skipped_pattern_count"]


@lru_cache(maxsize=1)
def _compiled() -> tuple[list[CompiledTech], _compile.JsSurface, int]:
    """Compile the dataset once per process — the per-pattern surfaces AND the ``js``
    Set. ``compile_all``/``compile_js_surface`` are pure and cheap, but re-parsing every
    enthec pattern on every ANALYZE call is wasted work; memoized here rather than on the
    compilers themselves because their argument (a dict) is unhashable, so ``lru_cache``
    cannot key on it directly. The third element is the combined RE2-reject count."""
    techs, _categories, _commit = _dataset.load_raw()
    compiled, skipped = _compile.compile_all(techs)
    js_surface, js_skipped = _compile.compile_js_surface(techs)
    return compiled, js_surface, skipped + js_skipped


def detect(host: str, signal: dict[str, Any], js_texts: list[str]) -> list[Detection]:
    """Match one host's signal bundle (+ its stored JS text) against every compiled
    fingerprint pattern and the ``js`` global-name Set, returning one `Detection` per
    matched technology."""
    _techs, categories, _commit = _dataset.load_raw()
    compiled, js_surface, _skipped = _compiled()
    return _match.match(compiled, categories, host, signal, js_texts, js_surface)


def dataset_commit() -> str:
    """The pinned enthec/webappanalyzer commit the vendored dataset was cut from."""
    return _dataset.load_raw()[2]


def skipped_pattern_count() -> int:
    """How many dataset patterns RE2 rejected at compile time (T4) - fed into the
    ANALYZE event so a widening reject rate is visible, not silent. Counts RE2 rejects
    only; the js surface's deliberate non-distinctive drops are by design, not rejects."""
    return _compiled()[2]
