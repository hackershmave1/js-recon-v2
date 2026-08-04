"""Local-disk blob storage — a SPIKE-only backing for the capture ingest path.

Mirrors :func:`recon.storage.put_blob` / :func:`recon.storage.get_blob` exactly
so it can be swapped in by attribute reassignment (see ``api/app.py``, gated on
``settings.enable_capture_ingest``). Its whole reason to exist is to PROVE the
analyze path has no *hard* dependency on S3/MinIO — the coupling is call-time
only. Not for production: no GC, no isolation guarantees beyond the filesystem.
"""

from __future__ import annotations

from pathlib import Path

from recon.config import get_settings
from recon.storage import object_key


def _root() -> Path:
    return Path(get_settings().capture_storage_dir)


def put_blob(tenant_id: str, run_id: str, kind: str, content: bytes) -> str:
    """Write bytes under the same content-addressed, tenant-scoped key
    :mod:`recon.storage` would use, but to local disk. Returns the key."""
    key = object_key(tenant_id, run_id, kind, content)
    dest = _root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return key


def get_blob(key: str) -> bytes:
    return (_root() / key).read_bytes()
