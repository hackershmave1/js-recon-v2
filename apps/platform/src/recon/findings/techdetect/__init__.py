"""In-house pure-Python technology fingerprinter over the vendored enthec dataset.

``detect(host, signal, js_texts)`` matches the dataset's Phase-1 surfaces (response
headers, cookie names, script URLs, ``<meta generator>``, and JS source via the
``scripts`` field) with ``google-re2`` (ReDoS-safe). No network, no secret storage:
input is only the allowlisted fingerprint-signal + already-stored JS bytes."""

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
def _compiled() -> tuple[list[CompiledTech], int]:
    """Compile the dataset once per process. ``compile_all`` is pure and cheap, but
    re-parsing every enthec pattern on every ANALYZE call is wasted work; memoized
    here rather than on ``compile_all`` itself because its argument (a dict) is
    unhashable, so ``lru_cache`` cannot key on it directly."""
    techs, _categories, _commit = _dataset.load_raw()
    return _compile.compile_all(techs)


def detect(host: str, signal: dict[str, Any], js_texts: list[str]) -> list[Detection]:
    """Match one host's signal bundle (+ its stored JS text) against every compiled
    fingerprint pattern, returning one `Detection` per matched technology."""
    _techs, categories, _commit = _dataset.load_raw()
    compiled, _skipped = _compiled()
    return _match.match(compiled, categories, host, signal, js_texts)


def dataset_commit() -> str:
    """The pinned enthec/webappanalyzer commit the vendored dataset was cut from."""
    return _dataset.load_raw()[2]


def skipped_pattern_count() -> int:
    """How many dataset patterns RE2 rejected at compile time (T4) - fed into the
    ANALYZE event so a widening reject rate is visible, not silent."""
    return _compiled()[1]
