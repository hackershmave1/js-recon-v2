"""Object-storage key convention (REQ-D2, REQ-S1).

Large/binary artifacts (raw JS, source maps, reconstructed sources, reports)
live in object storage and are referenced by key from a row — the bytes never
sit in Postgres. The key embeds the tenant id so isolation covers blobs too, not
just rows.

Key shape:  ``{tenant_id}/{run_id}/{kind}/{sha256}``
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from recon.config import get_settings

# Artifact kinds stored as blobs. Extended as later slices add source maps,
# reconstructed sources, and reports.
BLOB_KINDS = frozenset(
    {
        "input",
        "raw_js",
        "source_map",
        "reconstructed",
        "report",
        "assets",
        "spec",
        "capture-requests",
        # Run-level GraphQL operations located by analyze (enrichment C, export-only):
        # a JSON list the OpenAPI export unions across the run's assets.
        "graphql",
        # Per-run tech-detection signal (allowlisted headers + script URLs + meta
        # markers + cookie NAMES, keyed by host). Never any secret or raw HTML.
        "fingerprint-signal",
    }
)


# Streaming read size for hashing/uploading a file-backed blob. 1 MiB trades syscalls
# for transient RAM so a large artifact (a big source map, D37-L2) is keyed and stored
# without ever entering memory whole.
_STREAM_CHUNK_BYTES = 1024 * 1024


def object_key(tenant_id: str, run_id: str, kind: str, content: bytes) -> str:
    """Build a content-addressed, tenant-scoped object key for a blob."""
    if kind not in BLOB_KINDS:
        raise ValueError(f"unknown blob kind: {kind!r}")
    digest = hashlib.sha256(content).hexdigest()
    return f"{tenant_id}/{run_id}/{kind}/{digest}"


def object_key_for_file(tenant_id: str, run_id: str, kind: str, path: str) -> str:
    """``object_key`` for a blob whose bytes live in a local FILE, hashed by streaming.

    The file is read through sha256 in chunks, so a large artifact (a big source map,
    D37-L2) is never held in memory to be keyed. The digest — hence the key — is
    byte-identical to :func:`object_key` over the same bytes (sha256 is streaming-
    invariant), so a streamed put dedups against a bytes put of the same content."""
    if kind not in BLOB_KINDS:
        raise ValueError(f"unknown blob kind: {kind!r}")
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_STREAM_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return f"{tenant_id}/{run_id}/{kind}/{hasher.hexdigest()}"


@lru_cache
def _s3_client():
    settings = get_settings()
    # Path-style addressing + s3v4 so the same client works against MinIO locally
    # and S3 in production.
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket() -> None:
    """Create the artifact bucket if it doesn't exist yet (idempotent)."""
    client = _s3_client()
    bucket = get_settings().s3_bucket
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def put_blob(tenant_id: str, run_id: str, kind: str, content: bytes) -> str:
    """Store bytes under a content-addressed key and return the key (REQ-D2)."""
    key = object_key(tenant_id, run_id, kind, content)
    ensure_bucket()
    _s3_client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=content)
    return key


def get_blob(key: str) -> bytes:
    """Fetch a blob's bytes by key."""
    obj = _s3_client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    return obj["Body"].read()


def put_blob_from_path(tenant_id: str, run_id: str, kind: str, path: str) -> str:
    """Store a local file's bytes under a content-addressed key, streaming (REQ-D2).

    The file is hashed for the key (:func:`object_key_for_file`) then uploaded via the
    S3 managed transfer (``upload_fileobj`` — chunked/multipart), so neither step holds
    the whole blob in memory — the streaming twin of :func:`put_blob` for artifacts too
    large to buffer (a big source map, D37-L2). The stored object is byte-identical to
    ``put_blob`` of the same content; content-addressing keys on the sha256, not the S3
    ETag (which a multipart upload computes differently), so dedup is unaffected."""
    key = object_key_for_file(tenant_id, run_id, kind, path)
    ensure_bucket()
    with open(path, "rb") as handle:
        _s3_client().upload_fileobj(handle, get_settings().s3_bucket, key)
    return key


def download_blob_to_path(key: str, dest: str) -> None:
    """Stream a blob to a local file by key (the read twin of :func:`put_blob_from_path`).

    Uses the S3 managed transfer (``download_fileobj`` — chunked) so a large blob lands
    on disk without entering memory whole; the caller then reads it incrementally
    (D37-L2 recovers a big source map from the temp file, never ``get_blob`` whole)."""
    with open(dest, "wb") as handle:
        _s3_client().download_fileobj(get_settings().s3_bucket, key, handle)
