"""Search covers EVERY stored source-map-recovered original (not just the few the tree lists
because they carry findings), with exact lines; unstored bundles keep the listed fallback."""

from __future__ import annotations

import pytest

from recon.probe import recovered_store, source_search, sources
from recon.probe.recovered_store import Entry, Manifest
from recon.probe.sources import SourceContent, SourceFile

APP = "https://acme.io/app.js"
OLD = "https://acme.io/old.js"


@pytest.fixture
def run(monkeypatch):
    texts = {
        "k1": "export const a = 1;\nfetch('/api/token')",
        "k2": "nothing",
        "k3": "const t = 'token'",
    }
    listing = [
        SourceFile(path=APP, kind="asset", fetch_status="ok"),
        SourceFile(path=OLD, kind="asset", fetch_status="ok"),
        # listed (has findings) — under APP it's ALSO stored: must not be counted twice
        SourceFile(path="src/a.js", kind="source", fetch_status="ok", asset_url=APP),
        # listed under OLD, whose originals aren't stored: searched via recovery, as before
        SourceFile(path="old/legacy.js", kind="source", fetch_status="ok", asset_url=OLD),
    ]
    monkeypatch.setattr(sources, "list_sources", lambda _t, _r: listing)
    monkeypatch.setattr(
        sources, "asset_keys", lambda _t, _r: {APP: "t/r/input/app", OLD: "t/r/input/old"}
    )
    monkeypatch.setattr(source_search.storage, "list_keys", lambda prefix: {f"{prefix}app"})
    monkeypatch.setattr(
        recovered_store,
        "load",
        lambda key: (
            Manifest(
                entries=[
                    Entry("src/a.js", "src/a.js", "k1"),
                    Entry("src/b.js", "src/b.js", "k2"),
                    Entry("vendor/c.js", "vendor/c.js", "k3"),
                ],
                partial=False,
            )
            if key == "t/r/input/app"
            else None
        ),
    )
    monkeypatch.setattr(
        recovered_store,
        "read",
        lambda key, *, path: SourceContent(
            path=path, content=texts[key], truncated=False, formatted=True
        ),
    )
    monkeypatch.setattr(
        source_search.formatted_text,
        "load",
        lambda key, *, path: SourceContent(
            path=path, content="bundle text", truncated=False, formatted=True
        ),
    )
    recovered = []

    def get_content(_t, _r, path, asset_url=None):
        recovered.append(path)
        return SourceContent(path=path, content="var token;", truncated=False, formatted=True)

    monkeypatch.setattr(sources, "get_source_content", get_content)
    return recovered


def test_every_stored_original_is_searched_with_exact_lines(run):
    out = source_search.search("t", "r", "token")
    assert out is not None
    hits = {(m.path, m.asset_url, m.line, m.exact) for m in out.matches}
    assert ("src/a.js", APP, 2, True) in hits
    assert ("vendor/c.js", APP, 1, True) in hits  # unlisted in the tree, still found
    assert ("old/legacy.js", OLD, 1, True) in hits  # unstored bundle: listed fallback
    assert run == ["old/legacy.js"]  # only the unstored bundle's original was recovered


def test_a_listed_original_that_is_also_stored_is_counted_once(run):
    out = source_search.search("t", "r", "token")
    assert out is not None
    # 2 bundles + 3 stored originals of APP + 1 listed original of OLD
    assert out.files_total == 6 and out.files_scanned == 6
    assert sum(1 for m in out.matches if m.path == "src/a.js") == 1
