"""Serve a run's stored JavaScript source bytes for the UI code viewer (R5).

Read-only. Enumerates the source blobs a run already persisted — the raw
uploaded/fetched bundle at ``run.input_ref`` (legacy single-asset runs) or one
blob per crawl asset at ``run_asset.input_ref`` — and returns their bytes as
text. Mirrors ``reveal.py``: resolve the object key under RLS (``tenant_session``
/ ``run_assets.list_for_run``, both run-scoped), then read the blob AFTER the
session closes.

NOTE: this deliberately serves the raw source UNREDACTED — it is *not* a
secret-reveal action (cf. ``reveal.py`` / REQ-S3's audited disclosure). That is
consistent with ``/requests`` and ``/export``, which already expose
reconstructed detail without a per-item audit: the source is the analyst's own
captured artifact from an authorized target.

Out of scope here (distinct future capabilities): source-map-recovered original
files (``run.source_map_ref`` → ``sourcemapper.recover_sources``), and
deobfuscation (no deobfuscator exists in the backend)."""

from __future__ import annotations

from dataclasses import dataclass

from botocore.exceptions import ClientError

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.runs import assets as run_assets

# Bound the served text so one request can't stream an arbitrarily large decoded
# string. The real *memory* bound is the 10 MiB ingest cap
# (config.max_upload_bytes / max_fetch_bytes); this caps the RESPONSE.
_MAX_CONTENT_BYTES = 2 * 1024 * 1024

# analyze._SOURCE_NAME — the logical name of the single legacy bundle. It equals
# occurrences[].source_path for a legacy run and survives normalize_source_path
# unchanged, so the UI can join finding markers to this file by source_path.
_UPLOAD_PATH = "input.js"


@dataclass(frozen=True)
class SourceFile:
    path: str
    kind: str  # "asset" (one crawl asset) | "upload" (the legacy single bundle)
    fetch_status: str


@dataclass(frozen=True)
class SourceContent:
    path: str
    content: str
    truncated: bool


def list_sources(tenant_id: str, run_id: str) -> list[SourceFile] | None:
    """Every source file for a run, or ``None`` if the run is absent/invisible.

    Crawl runs (``run_asset`` rows exist) list one file per asset, named by its
    URL; legacy runs list the single stored bundle as ``"input.js"``. Reads no
    blob bytes (hence no size) to avoid an object-store GET per asset."""
    asset_rows = run_assets.list_for_run(tenant_id, run_id)
    if asset_rows:
        return [
            SourceFile(path=row.url, kind="asset", fetch_status=row.fetch_status)
            for row in asset_rows
        ]
    # No asset rows: a legacy run, or a run invisible to this tenant. The run row
    # distinguishes them (RLS hides another tenant's run -> None -> 404).
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        input_ref = run.input_ref
    if input_ref is None:
        return []  # legacy run that hasn't fetched/stored its bundle yet
    return [SourceFile(path=_UPLOAD_PATH, kind="upload", fetch_status="ok")]


def get_source_content(tenant_id: str, run_id: str, path: str) -> SourceContent | None:
    """One source file's bytes decoded to text, or ``None`` if the run/file is
    absent. ``path`` is only ever equality-matched against enumerated sources (an
    asset URL, or the literal ``"input.js"``); it never builds an object key."""
    key = _resolve_key(tenant_id, run_id, path)
    if key is None:
        return None
    try:
        raw = storage.get_blob(key)
    except ClientError:
        return None  # key vanished from the object store — treat as not found
    truncated = len(raw) > _MAX_CONTENT_BYTES
    # Slice the raw BYTES before decoding so the decoded string is bounded too.
    content = raw[:_MAX_CONTENT_BYTES].decode("utf-8", "replace")
    return SourceContent(path=path, content=content, truncated=truncated)


def _resolve_key(tenant_id: str, run_id: str, path: str) -> str | None:
    """The object key for ``path`` in this run, resolved under RLS. Run-scoped:
    the crawl match walks ``run_assets.list_for_run`` (already filtered to
    ``run_id``), never a bare ``url == path`` — ``run_asset.url`` is unique only
    per ``(run_id, url)``, so a bare match could serve another run's bytes."""
    asset_rows = run_assets.list_for_run(tenant_id, run_id)
    if asset_rows:
        for row in asset_rows:
            if row.url == path and row.fetch_status == "ok" and row.input_ref:
                return row.input_ref
        return None
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        input_ref = run.input_ref
    if path == _UPLOAD_PATH and input_ref:
        return input_ref
    return None
