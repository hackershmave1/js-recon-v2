"""Hermetic tests for the content-addressed object-key convention (REQ-D2, REQ-S1).

``object_key`` is pure (the S3/MinIO client isn't touched), and the tenant-scoped,
content-addressed key shape is the invariant that extends tenant isolation to blobs
— so it's pinned here in the fast lane rather than only under the integration store.
"""

from __future__ import annotations

import hashlib

import pytest

from recon.storage import BLOB_KINDS, object_key


def test_object_key_shape_is_tenant_run_kind_sha256():
    content = b"console.log(1)"
    digest = hashlib.sha256(content).hexdigest()
    assert object_key("tenant-1", "run-9", "raw_js", content) == f"tenant-1/run-9/raw_js/{digest}"


def test_object_key_is_content_addressed():
    # Identical bytes -> identical key (dedup); a one-byte change -> different key.
    same_a = object_key("t", "r", "input", b"payload")
    same_b = object_key("t", "r", "input", b"payload")
    changed = object_key("t", "r", "input", b"payloaX")
    assert same_a == same_b
    assert same_a != changed


def test_object_key_isolates_by_tenant_and_run():
    # The same bytes under a different tenant or run must never collide (REQ-S1).
    content = b"identical-bytes"
    assert object_key("t1", "r", "input", content) != object_key("t2", "r", "input", content)
    assert object_key("t", "r1", "input", content) != object_key("t", "r2", "input", content)


@pytest.mark.parametrize("kind", sorted(BLOB_KINDS))
def test_object_key_accepts_every_known_kind(kind):
    assert f"/{kind}/" in object_key("t", "r", kind, b"x")


def test_object_key_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown blob kind"):
        object_key("t", "r", "not_a_kind", b"x")
