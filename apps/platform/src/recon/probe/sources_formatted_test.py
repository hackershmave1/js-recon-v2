"""The viewer serves a bundle's stored formatted text instead of re-beautifying it per open; a run
analyzed before the cache existed fills it on first open."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from recon.findings import deobfuscate
from recon.probe import formatted_text, sources

RAW_KEY = "t/r/input/d1"


@pytest.fixture
def blobs(monkeypatch):
    store: dict[str, bytes] = {RAW_KEY: b"function a(){return 1}"}

    def get(key):
        if key not in store:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return store[key]

    monkeypatch.setattr(sources.storage, "get_blob", get)
    monkeypatch.setattr(formatted_text.storage, "get_blob", get)
    monkeypatch.setattr(formatted_text.storage, "put_blob_at", lambda k, c: store.__setitem__(k, c))
    monkeypatch.setattr(sources, "_resolve_key", lambda *_a: RAW_KEY)
    return store


def test_first_open_beautifies_once_and_stores_then_reuses(blobs, monkeypatch):
    calls = []
    real = deobfuscate.beautify
    monkeypatch.setattr(deobfuscate, "beautify", lambda s: calls.append(1) or real(s))
    first = sources.get_source_content("t", "r", "https://acme.io/a.js")
    second = sources.get_source_content("t", "r", "https://acme.io/a.js")
    assert len(calls) == 1
    assert first == second
    assert first is not None and first.formatted and "\n" in first.content
    assert "t/r/formatted/d1" in blobs


def test_a_stored_copy_is_served_as_is(blobs, monkeypatch):
    # The stored text IS what analyze located findings against: never re-derived on read.
    formatted_text.store(
        RAW_KEY,
        sources.SourceContent(path="x", content="stored\ntext", truncated=False, formatted=True),
    )
    monkeypatch.setattr(deobfuscate, "beautify", lambda _s: pytest.fail("re-beautified"))
    got = sources.get_source_content("t", "r", "https://acme.io/a.js")
    assert got is not None and got.content == "stored\ntext"
    assert got.path == "https://acme.io/a.js"


def test_served_text_is_shared_by_analyze_and_the_viewer(monkeypatch):
    assert deobfuscate.served_text("a();b()")[1] is True
    monkeypatch.setattr(deobfuscate, "beautify", lambda _s: None)
    assert deobfuscate.served_text("raw") == ("raw", False)


def test_a_stored_original_opens_by_identity_without_source_map_recovery(monkeypatch):
    from recon.probe import recovered_store

    monkeypatch.setattr(
        sources, "asset_keys", lambda *_a: {"https://acme.io/app.js": "t/r/input/b1"}
    )
    monkeypatch.setattr(
        recovered_store,
        "content",
        lambda key, path: (
            sources.SourceContent(path=path, content="src", truncated=False, formatted=True)
            if (key, path) == ("t/r/input/b1", "js/app.{hash}.js")
            else None
        ),
    )
    monkeypatch.setattr(
        sources, "recover_file_text", lambda *_a: pytest.fail("re-recovered the map")
    )
    monkeypatch.setattr(sources, "_resolve_key", lambda *_a: None)
    got = sources.get_source_content("t", "r", "js/app.{hash}.js", "https://acme.io/app.js")
    assert got is not None and got.content == "src"
