"""Serve a run's stored JavaScript source bytes for the UI code viewer (R5).

Read-only. Enumerates the source blobs a run already persisted — the raw
uploaded/fetched bundle at ``run.input_ref`` (legacy single-asset runs) or one
blob per crawl asset at ``run_asset.input_ref`` — and returns their bytes as
text. Mirrors ``reveal.py``: resolve the object key under RLS (``tenant_session``
/ ``run_assets.list_for_run``, both run-scoped), then read the blob AFTER the
session closes.

Also serves source-map-recovered ORIGINAL files (kind ``"source"``, e.g.
``webpack:/app/src/api.js``): the ones that carry >=1 finding are enumerated from
the findings' persisted ``occurrence.source_path`` (NOT by re-running recovery at
list time — that would spawn a Go subprocess per asset on every list), and their
bytes are recovered ON DEMAND from ``run_asset.source_map_ref`` /
``run.source_map_ref`` when one is opened. A bad/absent map yields "not found",
never a 500 (see ``_recovered_content``).

NOTE: this deliberately serves the raw source UNREDACTED — it is *not* a
secret-reveal action (cf. ``reveal.py`` / REQ-S3's audited disclosure). That is
consistent with ``/requests`` and ``/export``, which already expose
reconstructed detail without a per-item audit: the source is the analyst's own
captured artifact from an authorized target.

A raw no-map bundle is beautified ON DEMAND (recon.findings.deobfuscate — the same
deterministic beautify analyze runs before endpoint extraction) so it renders as
readable, multi-line text with the finding marks aligned; source-map-recovered
originals are served verbatim (never re-beautified)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from botocore.exceptions import ClientError
from sqlalchemy import select

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import deobfuscate, engines, sourcemapper
from recon.runs import assets as run_assets

# Bound the served text so one request can't stream an unbounded decoded string.
# D35: raised 2 MiB -> 12 MiB so a full minified bundle (<= the 10 MiB ingest cap
# config.max_upload_bytes / max_fetch_bytes) reaches the client viewer intact — it
# beautifies the bundle in a Web Worker and VIRTUALIZES the rows, so it no longer
# needs a server-truncated preview (the old 2 MiB cap silently corrupted a big bundle
# into an unreadable fragment). Beyond this the response is honestly `truncated`.
_MAX_CONTENT_BYTES = 12 * 1024 * 1024

# analyze._SOURCE_NAME — the logical name of the single legacy bundle. It equals
# occurrences[].source_path for a legacy run and survives normalize_source_path
# unchanged, so the UI can join finding markers to this file by source_path.
_UPLOAD_PATH = "input.js"


@dataclass(frozen=True)
class SourceFile:
    path: str
    kind: (
        str  # "asset" (one crawl asset) | "upload" (legacy bundle) | "source" (recovered original)
    )
    fetch_status: str
    # For kind="source" only: the asset whose source map recovered this original
    # (crawl), or None for a legacy run-level map. It is part of the finding<->source
    # join identity, so a path recovered by two different assets stays distinct
    # instead of collapsing to one node with the wrong bytes. None for asset/upload.
    asset_url: str | None = None


@dataclass(frozen=True)
class SourceContent:
    path: str
    content: str
    truncated: bool
    # D35: True when the SERVER already formatted this text (beautified a no-map bundle, or
    # a minified recovered original) — so its finding-line marks are ALIGNED and the client
    # must NOT re-beautify it (a re-beautify drops the marks). False only for a raw-served
    # bundle (over the 1 MiB beautify cap), whose marks sit on the useless raw line ~1 and
    # which the client formats in its Web Worker. Distinguishing this from a heuristic like
    # "has a long line" is why the flag is authoritative rather than client-guessed.
    formatted: bool


def list_sources(tenant_id: str, run_id: str) -> list[SourceFile] | None:
    """Every source file for a run, or ``None`` if the run is absent/invisible.

    Crawl runs (``run_asset`` rows exist) list one file per asset, named by its
    URL; legacy runs list the single stored bundle as ``"input.js"``. Either way,
    source-map-recovered originals that carry findings are appended (``kind
    "source"``). Reads no bundle bytes (hence no size) to avoid an object-store
    GET per asset, and runs NO recovery subprocess (that is on-demand only)."""
    asset_rows = run_assets.list_for_run(tenant_id, run_id)
    if asset_rows:
        files = [
            SourceFile(path=row.url, kind="asset", fetch_status=row.fetch_status)
            for row in asset_rows
        ]
        return files + (_recovered_sources(tenant_id, run_id) or [])
    # No asset rows: a legacy run, or a run invisible to this tenant. The run row
    # distinguishes them (RLS hides another tenant's run -> None -> 404).
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        input_ref = run.input_ref
    files = (
        []
        if input_ref is None
        else [SourceFile(path=_UPLOAD_PATH, kind="upload", fetch_status="ok")]
    )
    return files + (_recovered_sources(tenant_id, run_id) or [])


def _recovered_sources(tenant_id: str, run_id: str) -> list[SourceFile] | None:
    """The source-map-recovered originals that carry >=1 finding, derived from the
    persisted occurrence ``source_path`` values (design M1 — no recovery here).

    Identity is ``(source_path, asset_url)`` so an original recovered by two assets
    (a shared vendor chunk) stays distinct (M4). ``None`` if the run is invisible
    to the tenant. RLS-scoped: the query runs inside ``tenant_session``."""
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        rows = session.execute(
            select(
                models.FindingOccurrence.source_path,
                models.FindingOccurrence.run_asset_id,
            )
            .join(models.Finding, models.FindingOccurrence.finding)
            .where(
                models.Finding.run_id == str(run_id),
                models.FindingOccurrence.source_path.is_not(None),
                models.FindingOccurrence.source_path != _UPLOAD_PATH,
            )
            .distinct()
        ).all()
        asset_urls = {
            str(a.id): a.url
            for a in session.scalars(
                select(models.RunAsset).where(models.RunAsset.run_id == str(run_id))
            ).all()
        }
    seen: set[tuple[str, str | None]] = set()
    files: list[SourceFile] = []
    for source_path, run_asset_id in rows:
        asset_url = asset_urls.get(str(run_asset_id)) if run_asset_id is not None else None
        key = (source_path, asset_url)
        if key in seen:
            continue
        seen.add(key)
        files.append(
            SourceFile(path=source_path, kind="source", fetch_status="ok", asset_url=asset_url)
        )
    files.sort(key=lambda f: (f.path, f.asset_url or ""))
    return files


def get_source_content(
    tenant_id: str, run_id: str, path: str, asset_url: str | None = None
) -> SourceContent | None:
    """One source file's bytes decoded to text, or ``None`` if the run/file is
    absent. ``path`` is only ever equality-matched against enumerated sources (an
    asset URL, the literal ``"input.js"``, or a recovered original's path); it
    never builds an object key. A recovered original (``asset_url`` names the
    owning crawl asset, or ``None`` for a legacy run-level map) is recovered from
    its source map on demand."""
    key = _resolve_key(tenant_id, run_id, path)
    if key is not None:
        try:
            raw = storage.get_blob(key)
        except ClientError:
            return None  # key vanished from the object store — treat as not found
        # A raw bundle (upload/asset) with no source-map recovery is beautified on
        # demand so the served text matches analyze's beautified endpoint units and
        # the finding marks align — the SAME deterministic deobfuscate.beautify, so no
        # persisted blob. Over-cap/unavailable -> raw served (fail-soft). Recovered
        # originals go through _recovered_content, beautified only if themselves minified.
        text = raw.decode("utf-8", "replace")
        beautified = deobfuscate.beautify(text)
        # `formatted` iff the server actually beautified (a <=1 MiB bundle) — an oversized
        # bundle is served raw (beautified is None) and the client formats it (D35).
        return _content_from_text(
            path, beautified if beautified is not None else text, formatted=beautified is not None
        )
    # Not a stored asset/upload blob: try a source-map-recovered original.
    return _recovered_content(tenant_id, run_id, path, asset_url)


def _content_from_text(path: str, text: str, *, formatted: bool = False) -> SourceContent:
    """A decoded (possibly beautified) source string as bounded content: the response
    cap is applied to the UTF-8 bytes so a huge file can't stream an unbounded string.
    ``formatted`` records whether the server already beautified ``text`` (D35 — see
    ``SourceContent``)."""
    encoded = text.encode("utf-8")
    truncated = len(encoded) > _MAX_CONTENT_BYTES
    if truncated:
        text = encoded[:_MAX_CONTENT_BYTES].decode("utf-8", "replace")
    return SourceContent(path=path, content=text, truncated=truncated, formatted=formatted)


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


def _recovered_content(
    tenant_id: str, run_id: str, path: str, asset_url: str | None
) -> SourceContent | None:
    """Recover ``path`` from its source map on demand. Returns ``None`` (→ 404) if
    the map is missing, unparseable, times out, or the binary is absent — a bad map
    must never 500 the whole Sources tab (design M3). ``EngineNotAvailable`` and
    ``EngineTimeout`` subclass ``EngineError``, so one ``except`` covers all."""
    map_ref = _resolve_source_map_ref(tenant_id, run_id, asset_url)
    if map_ref is None:
        return None
    try:
        text = recover_file_text(map_ref, path)
    except engines.EngineError:
        return None
    if text is None:
        return None
    # A recovered original is served through the SAME `beautify_if_minified`
    # (`recover_file_text`) analyze ran, so its marks always align — the client must never
    # re-format it (D35): `formatted=True`.
    return _content_from_text(path, text, formatted=True)


def recover_file_text(map_ref: str, path: str) -> str | None:
    """The recovered original at ``path`` from the source-map blob ``map_ref``, beautified
    EXACTLY as the analyze stage scans it (`findings.analyze._analysis_units`) — the single
    definition of the recovered byte space. Analyze's secret offsets (D32-B1), this viewer,
    and the audited reveal (`recon.probe.reveal`) all reproduce byte-identical text, so an
    offset located at analyze time round-trips at reveal time.

    D37-L2 slice 2: the map is STREAMED to a temp file and ONLY ``path`` is recovered from
    it (`sourcemapper.recover_one_file`), so neither the whole map nor the whole recovered
    tree is held in the API process — narrowing the on-demand footprint from the whole tree
    to a single file (NOT eliminating it: one map-sized ``sourcesContent`` entry can still
    load ~the map cap, so a viewer of a large no-finding recovered file is the unbounded
    case; a per-file read cap is the deferred M5 follow-up — reveal is already bounded
    ~32 MiB by the analyze precondition). The recovered bytes go through the SAME
    deterministic `beautify_if_minified` analyze ran, so a minified vendor original's served
    line numbers still match its finding marks. Returns ``None``
    when the map blob is gone, recovers nothing, or has no such file; raises
    ``engines.EngineError`` on an unparseable map / absent binary so each caller decides its
    own fallback (this viewer → 404; reveal → fail-closed denial)."""
    with tempfile.TemporaryDirectory(prefix="smmap-") as workdir:
        map_path = os.path.join(workdir, "in.map")
        try:
            storage.download_blob_to_path(map_ref, map_path)
        except ClientError:
            return None  # the map blob vanished from the store — nothing to recover
        raw = sourcemapper.recover_one_file(map_path, path)
    if raw is None:
        return None
    return deobfuscate.beautify_if_minified(raw.decode("utf-8", "replace"))


def _resolve_source_map_ref(tenant_id: str, run_id: str, asset_url: str | None) -> str | None:
    """The source-map blob key for a recovered original, resolved under RLS. When
    ``asset_url`` is given it is the owning crawl asset's map (``(run_id, url)`` is
    unique, so ``.first()`` is exact); otherwise the legacy run-level map."""
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        if asset_url:
            row = session.scalars(
                select(models.RunAsset).where(
                    models.RunAsset.run_id == str(run_id),
                    models.RunAsset.url == asset_url,
                )
            ).first()
            return row.source_map_ref if row else None
        return run.source_map_ref
