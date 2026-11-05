"""Run-scoped full-text search across a run's sources (D52, rebuilt for speed and completeness).

The old grep re-beautified every bundle on every query (56 of ~60s on a 211-file run) and so had
to stop at 120 files / 200 matches. Now:

- a bundle is read as its STORED formatted text (recon.probe.formatted_text, written by analyze),
  so a match's line is the viewer's line; a bundle analyzed before that store existed is searched
  raw — still found, but with no line number (``exact=False``) — and never beautified inline (that
  would put minutes of single-threaded CPU on the API process);
- every listed file is scanned, bounded by a wall-clock budget instead of a file count; running out
  of budget returns what was found with ``partial=True`` and how many files were scanned;
- totals are honest: every hit is counted even past the display caps;
- one search per run at a time: the Sources box searches as you type, and a superseded query used to
  keep scanning to the end in the API's thread pool (its client had already stopped listening).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from recon import storage
from recon.probe import formatted_text, sources

MIN_QUERY = 2
MAX_MATCHES = 2000
MAX_PER_FILE = 200
SNIPPET = 240
BUDGET_SECONDS = 20.0
# Object-store reads run in parallel (I/O bound, the GIL is released); recovered originals do not:
# each one is a source-map subprocess, so they are read one at a time.
_READERS = 8
# A raw (unindexed) bundle can be one multi-megabyte line; never scan more than a response serves.
_RAW_SCAN_BYTES = 12 * 1024 * 1024


@dataclass(frozen=True)
class SourceMatch:
    path: str
    kind: str
    asset_url: str | None
    line: int | None  # the viewer's row; None when searched raw (not yet indexed)
    col: int
    snippet: str
    exact: bool


@dataclass
class SearchOutcome:
    matches: list[SourceMatch] = field(default_factory=list)
    total: int = 0
    files_total: int = 0
    files_scanned: int = 0
    unindexed: int = 0
    partial: bool = False


_inflight: dict[tuple[str, str], threading.Event] = {}
_inflight_lock = threading.Lock()


def _claim(tenant_id: str, run_id: str) -> threading.Event:
    """Cancel any search still running for this run and register this one."""
    mine = threading.Event()
    with _inflight_lock:
        previous = _inflight.get((tenant_id, run_id))
        if previous is not None:
            previous.set()
        _inflight[(tenant_id, run_id)] = mine
    return mine


def _release(tenant_id: str, run_id: str, mine: threading.Event) -> None:
    with _inflight_lock:
        if _inflight.get((tenant_id, run_id)) is mine:
            del _inflight[(tenant_id, run_id)]


def search(
    tenant_id: str, run_id: str, query: str, *, budget_seconds: float = BUDGET_SECONDS
) -> SearchOutcome | None:
    """Search the run's sources for ``query`` (case-insensitive substring). ``None`` when the run is
    absent/invisible (404); an empty outcome for a too-short query."""
    files = sources.list_sources(tenant_id, run_id)
    if files is None:
        return None
    needle = (query or "").strip().lower()
    outcome = SearchOutcome(files_total=len(files))
    if len(needle) < MIN_QUERY:
        return outcome
    cancelled = _claim(tenant_id, run_id)
    deadline = time.monotonic() + budget_seconds
    try:
        keys = sources.asset_keys(tenant_id, run_id)
        bundles = [f for f in files if f.kind != "source" and f.path in keys]
        originals = [f for f in files if f.kind == "source"]
        with ThreadPoolExecutor(max_workers=_READERS) as pool:
            reads = [pool.submit(_read_bundle, keys[f.path], f.path) for f in bundles]
            for f, read in zip(bundles, reads, strict=True):
                if cancelled.is_set() or time.monotonic() > deadline:
                    outcome.partial = True
                    for pending in reads:
                        pending.cancel()
                    break
                text, exact = read.result()
                if text is None:
                    continue
                outcome.unindexed += 0 if exact else 1
                _scan(outcome, f, text, needle, exact=exact)
                outcome.files_scanned += 1
        for f in originals if not outcome.partial else []:
            if cancelled.is_set() or time.monotonic() > deadline:
                outcome.partial = True
                break
            content = sources.get_source_content(tenant_id, run_id, f.path, f.asset_url)
            if content is not None:
                _scan(outcome, f, content.content, needle, exact=True)
            outcome.files_scanned += 1
    finally:
        _release(tenant_id, run_id, cancelled)
    return outcome


def _read_bundle(key: str, path: str) -> tuple[str | None, bool]:
    stored = formatted_text.load(key, path=path)
    if stored is not None:
        return stored.content, True
    try:
        raw = storage.get_blob(key)
    except Exception:
        return None, False
    return raw[:_RAW_SCAN_BYTES].decode("utf-8", "replace"), False


def _scan(
    outcome: SearchOutcome, f: sources.SourceFile, text: str, needle: str, *, exact: bool
) -> None:
    kept = 0
    # split("\n"), not splitlines(): the viewer numbers rows on "\n" only, so a "\r" or U+2028 inside
    # a line must not shift every later hit off its row.
    for line_no, line in enumerate(text.split("\n"), start=1):
        lowered = line.lower()
        col = lowered.find(needle)
        while col != -1:
            outcome.total += 1
            if kept < MAX_PER_FILE and len(outcome.matches) < MAX_MATCHES:
                outcome.matches.append(
                    SourceMatch(
                        path=f.path,
                        kind=f.kind,
                        asset_url=f.asset_url,
                        line=line_no if exact else None,
                        col=col,
                        snippet=_window(line, col, len(needle)),
                        exact=exact,
                    )
                )
                kept += 1
            col = lowered.find(needle, col + len(needle))


def _window(line: str, col: int, width: int) -> str:
    """The hit with context on both sides (a minified line can be megabytes: the first 240 chars of
    it usually don't contain the match at all)."""
    start = max(0, col - (SNIPPET - width) // 2)
    return line[start : start + SNIPPET].strip()
