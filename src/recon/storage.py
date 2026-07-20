"""Object-storage key convention (REQ-D2, REQ-S1).

Large/binary artifacts (raw JS, source maps, reconstructed sources, reports)
live in object storage and are referenced by key from a row — the bytes never
sit in Postgres. The key embeds the tenant id so isolation covers blobs too, not
just rows.

Key shape:  ``{tenant_id}/{run_id}/{kind}/{sha256}``
"""

from __future__ import annotations

import hashlib

# Artifact kinds stored as blobs. Extended as later slices add source maps,
# reconstructed sources, and reports.
BLOB_KINDS = frozenset({"input", "raw_js", "source_map", "reconstructed", "report"})


def object_key(tenant_id: str, run_id: str, kind: str, content: bytes) -> str:
    """Build a content-addressed, tenant-scoped object key for a blob."""
    if kind not in BLOB_KINDS:
        raise ValueError(f"unknown blob kind: {kind!r}")
    digest = hashlib.sha256(content).hexdigest()
    return f"{tenant_id}/{run_id}/{kind}/{digest}"
