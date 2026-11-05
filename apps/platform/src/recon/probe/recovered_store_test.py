"""Stored source-map-recovered originals: analyze keeps the served text of every original it
rebuilds, plus one list per bundle mapping each original's identity path to its stored copy, so the
viewer and search never re-run source-map recovery."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from recon.probe import recovered_store

BUNDLE = "t/r/input/b1"


@pytest.fixture
def blobs(monkeypatch):
    store: dict[str, bytes] = {}

    def put(tenant, run, kind, content):
        import hashlib

        key = f"{tenant}/{run}/{kind}/{hashlib.sha256(content).hexdigest()}"
        store[key] = content
        return key

    def get(key):
        if key not in store:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return store[key]

    monkeypatch.setattr(recovered_store.storage, "put_blob", put)
    monkeypatch.setattr(
        recovered_store.storage, "put_blob_at", lambda k, c: store.__setitem__(k, c)
    )
    monkeypatch.setattr(recovered_store.storage, "get_blob", get)
    return store


def test_originals_open_by_identity_path_even_when_the_name_is_hashed(blobs):
    # Findings name an original by its NORMALIZED path (content hashes collapsed): `app.{hash}.js`.
    # The stored list maps that identity to the real file, so the viewer no longer 404s it.
    recovered_store.store(
        BUNDLE, [("js/app.9f8e7d6c.js", "const a = 1;\nfetch('/api/x')")], partial=False
    )
    manifest = recovered_store.load(BUNDLE)
    assert manifest is not None
    [entry] = manifest.entries
    assert entry.rel == "js/app.9f8e7d6c.js"
    got = recovered_store.content(BUNDLE, entry.path)
    assert got is not None and got.content.endswith("fetch('/api/x')")
    assert got.formatted is True  # served as analyze located it: the client never re-formats


def test_two_files_with_one_identity_both_stay_searchable(blobs):
    recovered_store.store(
        BUNDLE, [("a/app.9f8e7d6c.js", "one"), ("a/app.1a2b3c4d.js", "two")], partial=False
    )
    manifest = recovered_store.load(BUNDLE)
    assert manifest is not None and len(manifest.entries) == 2
    assert (
        recovered_store.content(BUNDLE, manifest.entries[0].path).content == "one"
    )  # first wins on open


def test_no_list_means_not_stored_yet(blobs):
    assert recovered_store.load(BUNDLE) is None
    assert recovered_store.content(BUNDLE, "src/a.js") is None


def test_a_failed_store_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("object store down")

    monkeypatch.setattr(recovered_store.storage, "put_blob", boom)
    recovered_store.store(BUNDLE, [("a.js", "x")], partial=False)  # logged, not raised


def test_partial_recovery_is_recorded(blobs):
    recovered_store.store(BUNDLE, [("a.js", "x")], partial=True)
    manifest = recovered_store.load(BUNDLE)
    assert manifest is not None and manifest.partial is True
