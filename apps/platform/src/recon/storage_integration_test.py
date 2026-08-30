"""Integration round-trip for the streaming blob primitives (needs live MinIO/S3).

``object_key_for_file``'s key correctness is pinned hermetically in ``storage_test.py``;
this lane proves the actual S3 managed-transfer round-trip (``upload_fileobj`` /
``download_fileobj``) that the hermetic lane can't touch. Run with the integration store
env (see ``apps/platform/README``):

    RECON_S3_ENDPOINT_URL=http://localhost:9000 RECON_S3_ACCESS_KEY=recon \\
    RECON_S3_SECRET_KEY=recon-secret RECON_S3_BUCKET=recon-artifacts pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from recon import storage

pytestmark = pytest.mark.integration


def test_put_blob_from_path_round_trips(tmp_path):
    # A file larger than a single chunk streams up and back byte-identical, and the key
    # is the content-address of the same bytes (D37-L2: recover-from-streamed-blob).
    content = os.urandom(3 * 1024 * 1024 + 11)  # multi-chunk, deliberately non-aligned
    src = tmp_path / "src.bin"
    src.write_bytes(content)

    key = storage.put_blob_from_path("t-int", "r-int", "source_map", str(src))
    assert key == storage.object_key("t-int", "r-int", "source_map", content)

    dest = tmp_path / "dest.bin"
    storage.download_blob_to_path(key, str(dest))
    assert dest.read_bytes() == content


def test_streamed_put_dedups_with_bytes_put(tmp_path):
    # A streamed put and a bytes put of identical content resolve to the SAME key and
    # object (content-addressing keys on the sha256, not the multipart ETag), so mixing
    # the two paths never stores a duplicate.
    content = b"same-bytes-both-ways\n" * 2000
    src = tmp_path / "s.bin"
    src.write_bytes(content)

    streamed = storage.put_blob_from_path("t", "r", "input", str(src))
    buffered = storage.put_blob("t", "r", "input", content)
    assert streamed == buffered
    assert storage.get_blob(streamed) == content
