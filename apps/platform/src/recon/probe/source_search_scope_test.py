"""Search scope (All · This folder · This file): the folder is the Sources tree's folder — the SAME
segment rule (scheme stripped, empty segments dropped) — and covers its subfolders; "this file" is
the exact file (path + owning bundle)."""

from __future__ import annotations

import pytest

from recon.probe import recovered_store, source_search, sources
from recon.probe.recovered_store import Entry, Manifest
from recon.probe.source_search import Scope
from recon.probe.sources import SourceContent, SourceFile

APP = "https://acme.io/static/app.js"
OTHER = "http://cdn.acme.io/lib.js"


# Shared with the web tree's segmentsOf (SourcesPage.tsx) — keep both in step.
SEGMENT_VECTORS = [
    ("https://acme.io/static/app.js", ["acme.io", "static", "app.js"]),
    ("http://acme.io//static/app.js", ["acme.io", "static", "app.js"]),
    ("webpack://src/api.js", ["src", "api.js"]),
    (
        "node_modules/@statsig/client-core/src/a.js",
        ["node_modules", "@statsig", "client-core", "src", "a.js"],
    ),
    ("input.js", ["input.js"]),
]


@pytest.mark.parametrize(("path", "segments"), SEGMENT_VECTORS)
def test_segments_match_the_tree(path, segments):
    assert source_search.segments_of(path) == segments


@pytest.fixture
def run(monkeypatch):
    listing = [
        SourceFile(path=APP, kind="asset", fetch_status="ok"),
        SourceFile(path=OTHER, kind="asset", fetch_status="ok"),
    ]
    monkeypatch.setattr(sources, "list_sources", lambda _t, _r: listing)
    monkeypatch.setattr(
        sources, "asset_keys", lambda _t, _r: {APP: "t/r/input/app", OTHER: "t/r/input/other"}
    )
    monkeypatch.setattr(source_search.storage, "list_keys", lambda prefix: {f"{prefix}app"})
    monkeypatch.setattr(
        recovered_store,
        "load",
        lambda key: (
            Manifest(
                entries=[
                    Entry("src/api/a.js", "src/api/a.js", "ka"),
                    Entry("src/api/v2/b.js", "src/api/v2/b.js", "kb"),
                    Entry("src/ui/c.js", "src/ui/c.js", "kc"),
                ],
                partial=False,
            )
            if key == "t/r/input/app"
            else None
        ),
    )
    reads: list[str] = []

    def read_original(key, *, path):
        reads.append(path)
        return SourceContent(path=path, content="token", truncated=False, formatted=True)

    monkeypatch.setattr(recovered_store, "read", read_original)

    def read_bundle(key, *, path):
        reads.append(path)
        return SourceContent(path=path, content="token", truncated=False, formatted=True)

    monkeypatch.setattr(source_search.formatted_text, "load", read_bundle)
    return reads


def _paths(out):
    return sorted({m.path for m in out.matches})


def test_all_searches_everything(run):
    out = source_search.search("t", "r", "token")
    assert _paths(out) == sorted([APP, OTHER, "src/api/a.js", "src/api/v2/b.js", "src/ui/c.js"])


def test_folder_covers_its_subfolders_and_nothing_else(run):
    out = source_search.search("t", "r", "token", scope=Scope(folder="src/api"))
    assert _paths(out) == ["src/api/a.js", "src/api/v2/b.js"]
    assert (out.files_scanned, out.files_total) == (2, 2)
    assert sorted(run) == ["src/api/a.js", "src/api/v2/b.js"]  # out-of-scope files never read


def test_a_folder_of_a_bundle_url_uses_the_trees_segments(run):
    out = source_search.search("t", "r", "token", scope=Scope(folder="acme.io/static"))
    assert _paths(out) == [APP]


def test_this_file_is_exact_path_and_bundle(run):
    out = source_search.search("t", "r", "token", scope=Scope(path="src/ui/c.js", asset_url=APP))
    assert _paths(out) == ["src/ui/c.js"]
    out = source_search.search("t", "r", "token", scope=Scope(path="src/ui/c.js", asset_url=OTHER))
    assert out.matches == []  # same path under another bundle is a different file
    out = source_search.search("t", "r", "token", scope=Scope(path=APP))
    assert _paths(out) == [APP]


def test_folder_match_is_by_whole_segments_and_empty_path_means_all():
    f = SourceFile(path="src/apiV2/x.js", kind="source", fetch_status="ok", asset_url=APP)
    assert not Scope(folder="src/api").covers(f)  # not a prefix of the segment "apiV2"
    assert Scope(folder="/src/apiV2/").covers(f)  # client slashes tolerated
    assert Scope(path="").covers(f)  # an empty ?path= is no file scope
