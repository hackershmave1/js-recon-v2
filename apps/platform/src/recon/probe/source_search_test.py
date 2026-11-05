"""Run-scoped source search (D52, rebuilt): reads each bundle's STORED formatted text (never
re-beautifies), covers every listed file within a time budget, reports totals honestly, and a newer
search for the same run cancels an older one. Storage/DB are faked; the integration lane covers the
real reads."""

from __future__ import annotations

import threading

import pytest

from recon.findings import deobfuscate
from recon.probe import formatted_text, source_search, sources
from recon.probe.sources import SourceContent, SourceFile


@pytest.fixture
def run(monkeypatch):
    """A fake run: `files` (path -> served text) are indexed bundles, `raw` are bundles without a
    stored copy, `recovered` are source-map originals."""
    state: dict = {"files": {}, "raw": {}, "recovered": {}}

    def listing(_t, _r):
        out = [
            SourceFile(path=p, kind="asset", fetch_status="ok")
            for p in [*state["files"], *state["raw"]]
        ]
        out += [
            SourceFile(path=p, kind="source", fetch_status="ok", asset_url="b")
            for p in state["recovered"]
        ]
        return out

    monkeypatch.setattr(sources, "list_sources", listing)
    monkeypatch.setattr(
        sources,
        "asset_keys",
        lambda _t, _r: {p: f"t/r/input/{p}" for p in [*state["files"], *state["raw"]]},
    )

    def load(key, *, path):
        text = state["files"].get(key.rsplit("/", 1)[1])
        return (
            None
            if text is None
            else SourceContent(path=path, content=text, truncated=False, formatted=True)
        )

    monkeypatch.setattr(formatted_text, "load", load)
    monkeypatch.setattr(
        source_search.storage, "get_blob", lambda key: state["raw"][key.rsplit("/", 1)[1]].encode()
    )

    def recovered(_t, _r, path, asset_url=None):
        text = state["recovered"].get(path)
        return (
            None
            if text is None
            else SourceContent(path=path, content=text, truncated=False, formatted=True)
        )

    monkeypatch.setattr(sources, "get_source_content", recovered)
    monkeypatch.setattr(
        deobfuscate, "beautify", lambda _s: pytest.fail("search must never beautify")
    )
    return state


def test_indexed_bundle_matches_carry_viewer_line_numbers(run):
    run["files"]["a.js"] = "const x = 1;\nfetch('/api/token')\nnothing"
    out = source_search.search("t", "r", "TOKEN")
    assert out is not None
    [m] = out.matches
    assert (m.path, m.line, m.exact) == ("a.js", 2, True)
    assert "/api/token" in m.snippet


def test_unindexed_bundle_is_searched_raw_without_line_numbers(run):
    # A run analyzed before stored text existed: still searched (raw), never beautified inline.
    run["raw"]["old.js"] = "a();fetch('/api/token');b()"
    out = source_search.search("t", "r", "token")
    assert out is not None
    [m] = out.matches
    assert (m.line, m.exact) == (None, False)
    assert out.unindexed == 1


def test_every_file_is_scanned_no_file_cap(run):
    for i in range(300):
        run["files"][f"f{i}.js"] = "needle"
    out = source_search.search("t", "r", "needle")
    assert out is not None
    assert (out.files_scanned, out.files_total, out.total) == (300, 300, 300)
    assert out.partial is False


def test_total_counts_every_hit_beyond_the_display_caps(run):
    run["files"]["a.js"] = "\n".join(["x needle needle"] * 500)
    out = source_search.search("t", "r", "needle")
    assert out is not None
    assert out.total == 1000
    assert len(out.matches) == source_search.MAX_PER_FILE


def test_snippet_is_centred_on_the_hit_in_a_long_line(run):
    run["raw"]["min.js"] = "x" * 5000 + "NEEDLE" + "y" * 5000
    out = source_search.search("t", "r", "needle")
    assert out is not None
    assert "NEEDLE" in out.matches[0].snippet
    assert out.matches[0].col == 5000


def test_time_budget_returns_partial_results(run, monkeypatch):
    for i in range(10):
        run["files"][f"f{i}.js"] = "needle"
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(source_search.time, "monotonic", lambda: next(ticks))
    out = source_search.search("t", "r", "needle", budget_seconds=25)
    assert out is not None and out.partial
    assert out.files_scanned < out.files_total


def test_a_newer_search_cancels_the_older_one_for_the_same_run(run, monkeypatch):
    run["recovered"].update({"one.js": "needle", "two.js": "needle"})
    started, release = threading.Event(), threading.Event()
    real = sources.get_source_content

    def slow(*a, **k):
        started.set()
        release.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(sources, "get_source_content", slow)
    result: dict = {}
    first = threading.Thread(
        target=lambda: result.setdefault("first", source_search.search("t", "r", "needle"))
    )
    first.start()
    started.wait(5)
    monkeypatch.setattr(sources, "get_source_content", real)
    second = source_search.search("t", "r", "needle")
    release.set()
    first.join(5)
    assert result["first"].partial is True
    assert second is not None and second.partial is False


def test_recovered_originals_are_searched(run):
    run["recovered"]["webpack/src/api.js"] = "export const url = '/api/token'"
    out = source_search.search("t", "r", "token")
    assert out is not None
    assert [(m.path, m.kind, m.line) for m in out.matches] == [("webpack/src/api.js", "source", 1)]


def test_rows_are_numbered_like_the_viewer_on_newlines_only(run):
    # "\r" and U+2028 are not row breaks in the viewer (split("\n")), so they must not shift hits.
    run["files"]["a.js"] = "a\rb\u2028c\nneedle"
    out = source_search.search("t", "r", "needle")
    assert out is not None and out.matches[0].line == 2


def test_short_query_and_absent_run(run, monkeypatch):
    out = source_search.search("t", "r", "a")
    assert out is not None and out.matches == [] and out.files_scanned == 0
    monkeypatch.setattr(sources, "list_sources", lambda _t, _r: None)
    assert source_search.search("t", "r", "token") is None


def test_the_budget_holds_inside_one_huge_file(run, monkeypatch):
    # Code review H2: the budget used to be checked only between files.
    run["files"]["min.js"] = "\n".join(["ab ab ab"] * 20000)
    ticks = iter(range(10**6))
    monkeypatch.setattr(source_search.time, "monotonic", lambda: next(ticks))
    out = source_search.search("t", "r", "ab", budget_seconds=50)
    assert out is not None and out.partial
    assert out.files_scanned == 0


def test_a_hung_read_does_not_outlive_the_budget(run, monkeypatch):
    release = threading.Event()

    def hang(_key, *, path):
        release.wait(10)

    monkeypatch.setattr(formatted_text, "load", hang)
    run["files"]["a.js"] = "needle"
    out = source_search.search("t", "r", "needle", budget_seconds=0.3)
    release.set()
    assert out is not None and out.partial


def test_reads_are_windowed_not_all_held_at_once(run, monkeypatch):
    # Code review H1: every file's text used to stay alive until the search ended.
    live, peak = set(), [0]
    lock = threading.Lock()
    for i in range(60):
        run["files"][f"f{i}.js"] = "x"

    def load(key, *, path):
        with lock:
            live.add(path)
            peak[0] = max(peak[0], len(live))
        return SourceContent(path=path, content="x", truncated=False, formatted=True)

    real_scan = source_search._scan

    def scan(outcome, f, *a, **k):
        real_scan(outcome, f, *a, **k)
        with lock:
            live.discard(f.path)

    monkeypatch.setattr(formatted_text, "load", load)
    monkeypatch.setattr(source_search, "_scan", scan)
    source_search.search("t", "r", "needle")
    assert peak[0] <= source_search._READERS * 2 + 1


def test_files_total_counts_only_searchable_files(run, monkeypatch):
    # A failed-fetch asset is in the tree but has no bytes: not "1 of 2 searched".
    run["files"]["ok.js"] = "needle"
    real = sources.list_sources
    monkeypatch.setattr(
        sources,
        "list_sources",
        lambda t, r: [
            *real(t, r),
            SourceFile(path="failed.js", kind="asset", fetch_status="failed"),
        ],
    )
    out = source_search.search("t", "r", "needle")
    assert out is not None
    assert (out.files_scanned, out.files_total, out.partial) == (1, 1, False)


def test_two_clients_on_one_run_do_not_cancel_each_other(run, monkeypatch):
    run["recovered"]["one.js"] = "needle"
    started, release = threading.Event(), threading.Event()
    real = sources.get_source_content

    def slow(*a, **k):
        started.set()
        release.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(sources, "get_source_content", slow)
    result: dict = {}
    t = threading.Thread(
        target=lambda: result.setdefault("a", source_search.search("t", "r", "needle", client="A"))
    )
    t.start()
    started.wait(5)
    monkeypatch.setattr(sources, "get_source_content", real)
    source_search.search("t", "r", "needle", client="B")
    release.set()
    t.join(5)
    assert result["a"].partial is False
