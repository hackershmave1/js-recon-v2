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
BLOB_KINDS = frozenset({"input", "raw_js", "source_map", "reconstructed", "report"})


def object_key(tenant_id: str, run_id: str, kind: str, content: bytes) -> str:
    """Build a content-addressed, tenant-scoped object key for a blob."""
    if kind not in BLOB_KINDS:
        raise ValueError(f"unknown blob kind: {kind!r}")
    digest = hashlib.sha256(content).hexdigest()
    return f"{tenant_id}/{run_id}/{kind}/{digest}"


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
