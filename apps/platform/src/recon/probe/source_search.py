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

import functools
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from recon import storage
from recon.probe import formatted_text, recovered_store, sources

MIN_QUERY = 2
MAX_MATCHES = 2000
MAX_PER_FILE = 200
SNIPPET = 240
BUDGET_SECONDS = 20.0
# Object-store reads (bundles and stored originals) run in parallel: I/O bound, the GIL is released.
# Only an unstored bundle's listed originals are recovered (a source-map subprocess), one at a time.
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


@dataclass(frozen=True)
class Scope:
    """What to search: everything (default), one folder of the Sources tree and its subfolders
    (``folder`` = the tree's segments joined by "/"), or one exact file (``path`` + the bundle it
    was recovered from; ``asset_url=None`` for a bundle itself)."""

    folder: str | None = None
    path: str | None = None
    asset_url: str | None = None

    def covers(self, f: sources.SourceFile) -> bool:
        if self.path is not None:
            return f.path == self.path and (f.asset_url or None) == (self.asset_url or None)
        if self.folder:
            return "/".join(segments_of(f.path)).startswith(self.folder.strip("/") + "/")
        return True


_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def segments_of(path: str) -> list[str]:
    """The Sources tree's folder segments (web SourcesPage.tsx `segmentsOf`): scheme stripped,
    empty segments dropped — so http:// and https:// assets of one host share a folder, as in the
    tree. Shared test vectors keep the two in step."""
    segments = [seg for seg in _SCHEME.sub("", path).split("/") if seg]
    return segments or [path]


@dataclass
class SearchOutcome:
    matches: list[SourceMatch] = field(default_factory=list)
    total: int = 0
    files_total: int = 0
    files_scanned: int = 0
    unindexed: int = 0
    partial: bool = False


# Keyed by the searching CLIENT (one Sources page), never just the run: two people (or tabs)
# searching one run must not cancel each other — only a page's own superseded query is dropped.
_inflight: dict[tuple[str, str, str], threading.Event] = {}
_inflight_lock = threading.Lock()


def _claim(key: tuple[str, str, str]) -> threading.Event:
    """Cancel this client's previous search for the run, if still running, and register this one."""
    mine = threading.Event()
    with _inflight_lock:
        previous = _inflight.get(key)
        if previous is not None:
            previous.set()
        _inflight[key] = mine
    return mine


def _release(key: tuple[str, str, str], mine: threading.Event) -> None:
    with _inflight_lock:
        if _inflight.get(key) is mine:
            del _inflight[key]


class _Stop(Exception):
    """The budget ran out or a newer query superseded this one, mid-file."""


def search(
    tenant_id: str,
    run_id: str,
    query: str,
    *,
    client: str = "",
    scope: Scope | None = None,
    budget_seconds: float = BUDGET_SECONDS,
) -> SearchOutcome | None:
    """Search the run's sources for ``query`` (case-insensitive substring). ``None`` when the run is
    absent/invisible (404); an empty outcome for a too-short query. ``client`` identifies the
    searching page so its own superseded query is cancelled (and nobody else's)."""
    files = sources.list_sources(tenant_id, run_id)
    if files is None:
        return None
    keys = sources.asset_keys(tenant_id, run_id)
    # Only files that HAVE content count: a failed-fetch asset is listed in the tree but has no
    # bytes, so "searched X of Y" must not read as incomplete because of it.
    scope = scope or Scope()
    bundles = [f for f in files if f.kind != "source" and f.path in keys]
    # EVERY stored original (recon.probe.recovered_store), not only the few the tree lists because
    # they carry findings; a bundle whose originals aren't stored yet (a run analyzed before they
    # were) still has its listed ones searched by recovering them, as before.
    stored = _stored_originals(tenant_id, run_id, bundles, keys)
    stored_bundles = {f.asset_url for f, _key in stored}
    listed = [f for f in files if f.kind == "source" and f.asset_url not in stored_bundles]
    # Out-of-scope files are never read (a stored original's bundle must still be found above, so
    # the scope filters files, not the bundles their originals hang off).
    stored = [(f, key) for f, key in stored if scope.covers(f)]
    listed = [f for f in listed if scope.covers(f)]
    bundles = [f for f in bundles if scope.covers(f)]
    outcome = SearchOutcome(files_total=len(bundles) + len(stored) + len(listed))
    needle = (query or "").strip().lower()
    if len(needle) < MIN_QUERY:
        return outcome
    registry_key = (tenant_id, run_id, client)
    cancelled = _claim(registry_key)
    deadline = time.monotonic() + budget_seconds

    def check() -> None:
        if cancelled.is_set() or time.monotonic() > deadline:
            raise _Stop

    try:
        reads: list[tuple[sources.SourceFile, Callable[[], tuple[str | None, bool]]]] = [
            (f, functools.partial(_read_bundle, keys[f.path], f.path)) for f in bundles
        ]
        reads += [(f, functools.partial(_read_original, key, f.path)) for f, key in stored]
        _scan_windowed(outcome, reads, needle, check, deadline)
        for f in listed:
            check()
            content = sources.get_source_content(tenant_id, run_id, f.path, f.asset_url)
            if content is not None:
                _scan(outcome, f, content.content, needle, exact=True, check=check)
            outcome.files_scanned += 1
    except _Stop:
        outcome.partial = True
    finally:
        _release(registry_key, cancelled)
    return outcome


def _stored_originals(
    tenant_id: str, run_id: str, bundles: list[sources.SourceFile], keys: dict[str, str]
) -> list[tuple[sources.SourceFile, str]]:
    """``(file, stored key)`` for every stored original of the run's bundles: ONE listing finds
    which bundles have a stored list, then one read per such bundle (12 of 211 on a real run)."""
    prefix = f"{tenant_id}/{run_id}/originals/"
    try:
        have = {key[len(prefix) :] for key in storage.list_keys(prefix)}
    except Exception:
        return []
    out: list[tuple[sources.SourceFile, str]] = []
    for bundle in bundles:
        bundle_key = keys[bundle.path]
        if bundle_key.rsplit("/", 1)[1] not in have:
            continue
        manifest = recovered_store.load(bundle_key)
        for entry in manifest.entries if manifest else []:
            f = sources.SourceFile(
                path=entry.path, kind="source", fetch_status="ok", asset_url=bundle.path
            )
            out.append((f, entry.key))
    return out


def _read_original(key: str, path: str) -> tuple[str | None, bool]:
    stored = recovered_store.read(key, path=path)
    return (stored.content, True) if stored is not None else (None, True)


def _scan_windowed(
    outcome: SearchOutcome,
    reads: list[tuple[sources.SourceFile, Callable[[], tuple[str | None, bool]]]],
    needle: str,
    check: Callable[[], None],
    deadline: float,
) -> None:
    """Read files in parallel through a small window: each file's text is dropped once scanned,
    so a run never holds every (up to 12 MiB) copy at once."""
    pool = ThreadPoolExecutor(max_workers=_READERS)
    window: deque[tuple[sources.SourceFile, Future[tuple[str | None, bool]]]] = deque()
    todo = iter(reads)
    try:
        for f, read_fn in todo:
            window.append((f, pool.submit(read_fn)))
            if len(window) >= _READERS * 2:
                break
        while window:
            f, read = window.popleft()
            check()
            try:
                text, exact = read.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                raise _Stop from None
            nxt = next(todo, None)
            if nxt is not None:
                window.append((nxt[0], pool.submit(nxt[1])))
            if text is not None:
                outcome.unindexed += 0 if exact else 1
                _scan(outcome, f, text, needle, exact=exact, check=check)
            outcome.files_scanned += 1
    finally:
        # Never wait on a hung read: queued reads are dropped, a running one finishes in the background.
        pool.shutdown(wait=False, cancel_futures=True)


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
    outcome: SearchOutcome,
    f: sources.SourceFile,
    text: str,
    needle: str,
    *,
    exact: bool,
    check: Callable[[], None] = lambda: None,
) -> None:
    kept = 0
    # split("\n"), not splitlines(): the viewer numbers rows on "\n" only, so a "\r" or U+2028 inside
    # a line must not shift every later hit off its row.
    for line_no, line in enumerate(text.split("\n"), start=1):
        if line_no % 256 == 0:
            check()  # the budget/cancel bound holds inside one huge file too
        lowered = line.lower()
        if kept >= MAX_PER_FILE or len(outcome.matches) >= MAX_MATCHES:
            # Nothing more to show from here: count the rest in C, not hit by hit (a 2-char needle
            # in a 12 MiB minified line is millions of hits).
            outcome.total += lowered.count(needle)
            continue
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
            elif kept >= MAX_PER_FILE or len(outcome.matches) >= MAX_MATCHES:
                outcome.total += lowered.count(needle, col + len(needle))
                break
            col = lowered.find(needle, col + len(needle))


def _window(line: str, col: int, width: int) -> str:
    """The hit with context on both sides (a minified line can be megabytes: the first 240 chars of
    it usually don't contain the match at all)."""
    start = max(0, col - (SNIPPET - width) // 2)
    return line[start : start + SNIPPET].strip()
