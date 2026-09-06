"""Hermetic tests for the content-addressed object-key convention (REQ-D2, REQ-S1).

``object_key`` is pure (the S3/MinIO client isn't touched), and the tenant-scoped,
content-addressed key shape is the invariant that extends tenant isolation to blobs
— so it's pinned here in the fast lane rather than only under the integration store.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from botocore.exceptions import ClientError

from recon import storage
from recon.storage import BLOB_KINDS, BlobPurgeError, object_key, object_key_for_file


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


def test_object_key_for_file_matches_object_key(tmp_path):
    # D37-L2 / R6: the streaming (file) hash must yield the SAME content-addressed key
    # as one-shot hashing the bytes, so a streamed put dedups identically to a bytes put
    # (content-addressing keys on the sha256, which is streaming-invariant).
    content = b"console.log(42);\n" * 100
    path = tmp_path / "blob.bin"
    path.write_bytes(content)
    assert object_key_for_file("t1", "r9", "source_map", str(path)) == object_key(
        "t1", "r9", "source_map", content
    )


def test_object_key_for_file_hashes_across_chunk_boundaries(tmp_path):
    # Content larger than the internal read chunk must hash identically — the incremental
    # sha256 has to span chunk boundaries (the streaming property this whole slice rests on).
    content = os.urandom(1024 * 1024 + 7)  # > 1 MiB, deliberately not chunk-aligned
    path = tmp_path / "big.bin"
    path.write_bytes(content)
    assert object_key_for_file("t", "r", "reconstructed", str(path)) == object_key(
        "t", "r", "reconstructed", content
    )


def test_object_key_for_file_rejects_unknown_kind(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="unknown blob kind"):
        object_key_for_file("t", "r", "not_a_kind", str(path))


# --------------------------------------------------------------------------- #
# delete_run_blobs — the REQ-S4 purge sweep (D47). Hermetic: a fake S3 client
# stands in for _s3_client so the paginator + batched-delete + Errors handling
# are pinned in the fast lane; the real-MinIO round-trip lives in the integration
# suite. Keeps this file's "no live S3" discipline.
# --------------------------------------------------------------------------- #


class _FakeS3:
    """Minimal boto3-S3 stand-in for the list+delete sweep. Paginates its key set at
    ``page_size`` (default 1000, as real S3 does) and records every delete batch + the
    prefix it was listed with, so a test can assert the sweep's prefix, pagination, and
    1000-key batching. ``fail_keys`` simulates ``delete_objects``' HTTP-200-with-``Errors``
    partial-failure shape."""

    def __init__(self, keys, *, page_size=1000, fail_keys=frozenset(), delete_raises=None):
        self._keys = list(keys)
        self._page_size = page_size
        self._fail_keys = frozenset(fail_keys)
        self._delete_raises = delete_raises  # an exception the whole delete_objects call raises
        self.listed_prefixes: list[str] = []
        self.delete_batches: list[list[str]] = []

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        outer = self

        class _Paginator:
            def paginate(self, *, Bucket, Prefix):  # noqa: N803 (boto3 kwarg name)
                outer.listed_prefixes.append(Prefix)
                matched = [k for k in outer._keys if k.startswith(Prefix)]
                if not matched:
                    yield {}  # a page with no "Contents" (empty prefix), as S3 returns
                    return
                for i in range(0, len(matched), outer._page_size):
                    yield {"Contents": [{"Key": k} for k in matched[i : i + outer._page_size]]}

        return _Paginator()

    def delete_objects(self, *, Bucket, Delete):  # noqa: N803 (boto3 kwarg names)
        if self._delete_raises is not None:
            raise self._delete_raises
        keys = [obj["Key"] for obj in Delete["Objects"]]
        self.delete_batches.append(keys)
        errors = [{"Key": k, "Code": "AccessDenied"} for k in keys if k in self._fail_keys]
        return {"Errors": errors} if errors else {}


def test_delete_run_blobs_sweeps_only_the_run_prefix_across_pages_and_batches(monkeypatch):
    # >1000 objects spread over several LIST pages must delete in ≤1000-key batches, and the
    # sweep must touch ONLY keys under the exact "{tenant}/{run}/" prefix — decoys under a
    # sibling run, a prefix-lookalike run, and another tenant are left untouched (REQ-S4 safety).
    target = [object_key("t1", "r1", "input", f"asset-{i}".encode()) for i in range(2500)]
    decoys = [
        object_key("t1", "r2", "input", b"other-run"),
        object_key("t1", "r1x", "input", b"prefix-lookalike"),  # "t1/r1x/" not under "t1/r1/"
        object_key("t2", "r1", "input", b"other-tenant"),
    ]
    fake = _FakeS3(target + decoys, page_size=400)  # page_size<1000 proves batching ≠ paging
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    deleted = storage.delete_run_blobs("t1", "r1")

    assert deleted == 2500
    assert fake.listed_prefixes == ["t1/r1/"]  # exact prefix, trailing slash
    assert [len(b) for b in fake.delete_batches] == [1000, 1000, 500]  # 3 batches at the limit
    swept = {k for batch in fake.delete_batches for k in batch}
    assert swept == set(target)  # no decoy (sibling run / lookalike / other tenant) was deleted


def test_delete_run_blobs_raises_on_partial_delete_errors(monkeypatch):
    # delete_objects returns HTTP 200 with a per-key Errors[] on partial failure — a bare
    # try/except never sees it. The sweep must surface it as BlobPurgeError so the purge is
    # never silently incomplete (H1 / REQ-S4 MUST).
    keys = [object_key("t", "r", "input", f"k{i}".encode()) for i in range(3)]
    fake = _FakeS3(keys, fail_keys={keys[1]})
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    with pytest.raises(BlobPurgeError) as excinfo:
        storage.delete_run_blobs("t", "r")
    # Structured counts ride on the exception so delete_session can log them (M1): 2 of 3 gone.
    assert excinfo.value.prefix == "t/r/"
    assert excinfo.value.failed == 1
    assert excinfo.value.deleted == 2
    # ...and WHICH key survived + WHY, not just the count, so the operator can remediate (H1).
    assert excinfo.value.sample == [{"key": keys[1], "code": "AccessDenied"}]
    assert fake.delete_batches  # a best-effort delete WAS attempted before raising


def test_delete_run_blobs_empty_prefix_deletes_nothing(monkeypatch):
    # A run whose blobs are already gone (or never existed): list yields no Contents, so no
    # delete_objects call is made and the count is 0 (no spurious empty-batch delete).
    fake = _FakeS3([object_key("t", "other", "input", b"x")])
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    assert storage.delete_run_blobs("t", "r") == 0
    assert fake.delete_batches == []


def test_delete_run_blobs_no_spurious_delete_at_exact_batch_multiple(monkeypatch):
    # Exactly _DELETE_BATCH_MAX keys: the in-loop flush empties `batch`, so the trailing _purge
    # sees [] and issues NO second (empty) delete_objects call. Pins the batch-boundary edge.
    n = storage._DELETE_BATCH_MAX
    keys = [object_key("t", "r", "input", f"k{i}".encode()) for i in range(n)]
    fake = _FakeS3(keys)  # page_size defaults to 1000 == _DELETE_BATCH_MAX
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    assert storage.delete_run_blobs("t", "r") == n
    assert len(fake.delete_batches) == 1  # one full batch, no spurious empty follow-up call


def test_delete_run_blobs_propagates_when_delete_objects_raises(monkeypatch):
    # A whole-call failure (e.g. missing s3:DeleteObject) raises rather than returning Errors[];
    # the sweep lets it propagate, so delete_session's broad except logs it + still 204s the delete.
    boom = ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObjects")
    fake = _FakeS3([object_key("t", "r", "input", b"x")], delete_raises=boom)
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    with pytest.raises(ClientError):
        storage.delete_run_blobs("t", "r")
