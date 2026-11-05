"""The stored formatted text of a source blob: written once (at analyze, or lazily by the viewer),
read back by the viewer and search so neither re-runs the beautifier."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from recon.probe import formatted_text
from recon.probe.sources import SourceContent


@pytest.fixture
def store(monkeypatch):
    blobs: dict[str, bytes] = {}

    def put(key, content):
        blobs[key] = content

    def get(key):
        if key not in blobs:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return blobs[key]

    monkeypatch.setattr(formatted_text.storage, "put_blob_at", put)
    monkeypatch.setattr(formatted_text.storage, "get_blob", get)
    return blobs


def test_key_sits_beside_the_input_blob_with_the_same_digest():
    assert formatted_text.formatted_key("t/r/input/abc123") == "t/r/formatted/abc123"
    assert formatted_text.formatted_key("t/r/raw_js/abc123") == "t/r/formatted/abc123"


def test_round_trip_keeps_text_and_flags(store):
    content = SourceContent(path="a.js", content="line1\nline2", truncated=False, formatted=True)
    formatted_text.store("t/r/input/d1", content)
    got = formatted_text.load("t/r/input/d1", path="a.js")
    assert got == content


def test_missing_or_corrupt_blob_is_a_miss_not_an_error(store):
    assert formatted_text.load("t/r/input/none", path="a.js") is None
    store["t/r/formatted/bad"] = b"not json"
    assert formatted_text.load("t/r/input/bad", path="a.js") is None


def test_a_failed_write_never_raises(monkeypatch):
    # A transient object-store outage must not fail analysis (EndpointConnectionError is not a
    # ClientError) or the viewer: the text is simply re-derived next time.
    def boom(*_a, **_k):
        raise EndpointConnectionError(endpoint_url="http://minio")

    monkeypatch.setattr(formatted_text.storage, "put_blob_at", boom)
    content = SourceContent(path="a.js", content="x", truncated=False, formatted=True)
    formatted_text.store("t/r/input/d1", content)  # no exception
