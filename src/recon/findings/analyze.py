"""Analyze stage — the in-process half of "one JS file -> findings".

Reads a JS blob, extracts its network calls (Vespasian), normalizes each into
its REQ-D3 identity, and writes them through the transactional outbox (REQ-A3).
Emits a coverage event with attributed-vs-unattributed counts so coverage is
reported honestly (REQ-C2). Idempotent: a stage retry re-emits the same hashes
and the outbox upserts are no-ops.

A crawl run (``run_asset`` rows present, Slice Y) loops every fetched asset
through ``_analyze_assets``, tagging each occurrence with that asset's
``run_asset_id``/``asset_url`` — the same endpoint sighted on two assets still
dedupes to one finding with two occurrences (Task 2's asset dimension). An
upload/single-URL run (no ``run_asset`` rows) analyzes ``run.input_ref`` as one
unit, unchanged. Both paths share the per-blob work via ``_analyze_blob``.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis import Redis

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import AssetStatus, FindingType
from recon.events.log import RecordedEvent, publish, record_event
from recon.findings import engines, kingfisher, normalize, sourcemapper, store
from recon.findings.extract import RawEndpoint, extract
from recon.findings.kingfisher import RawSecret
from recon.observability import get_logger
from recon.progress import heartbeat as progress
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries

log = get_logger("recon.findings.analyze")

# Fallback source path when no source map recovers the real per-file paths — the
# whole bundle is one logical source. Sourcemapper replaces this with real paths.
_SOURCE_NAME = "input.js"


@dataclass(frozen=True)
class FileCoverage:
    """Per-file honesty counter (REQ-C2): how many calls in one source file were
    attributed to an endpoint vs. left un-attributed. Keyed by the same normalized
    path the finding carries, so a reader can see *which* file has unmapped calls
    (the input the wrapper-teaching SHOULD acts on)."""

    path: str
    attributed: int
    unattributed: int


@dataclass(frozen=True)
class Coverage:
    attributed: int
    unattributed: int
    findings_written: int
    secrets: int = 0
    # Honest engine status (REQ-C2/§5): a scanner that was absent must not be
    # reported as "no secrets". Reachable values are "ok" and "unavailable" — a
    # genuine engine error/timeout raises before a Coverage is ever returned.
    secrets_engine: str = "ok"
    # Source-map honesty: how many original files were recovered, and how the map
    # was handled (none | uploaded | inline | unavailable | inline-error). REQ-D5
    # must NOT treat map-scoped endpoint coverage as full-bundle coverage.
    sources_recovered: int = 0
    source_map: str = "none"
    # Per-file breakdown of the attributed/unattributed totals (REQ-C2 is a
    # per-file counter — a bundle-wide sum hides which file needs attention).
    files: tuple[FileCoverage, ...] = ()


def analyze_run(
    redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None = None
) -> Coverage:
    """Analyze the run's JS and persist its findings.

    A crawl run (``run_asset`` rows present, Slice Y) loops every fetched-but-
    not-yet-analyzed asset through ``_analyze_assets``: each asset's blob is
    analyzed on its own and its findings tagged with that asset's
    ``run_asset_id``/``asset_url`` — the SAME endpoint sighted on two assets
    still dedupes to one ``finding`` with two occurrences (Task 2's asset
    dimension). Idempotent per asset (an analyze-terminal row is skipped, never
    re-analyzed), best-effort (one asset's failure does not abort the run),
    heartbeating, and cooperatively interruptible (REQ-A4).

    An upload/single-URL run (no ``run_asset`` rows) falls through unchanged to
    the legacy path below: analyze ``run.input_ref`` as one unit. No input ->
    no-op."""
    rows = run_assets.list_for_run(tenant_id, run_id)
    if rows:
        return _analyze_assets(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, rows=rows)
    # ---- legacy single-blob path below (unchanged) ----
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        input_ref = run.input_ref if run is not None else None
        source_map_ref = run.source_map_ref if run is not None else None
    if not input_ref:
        return Coverage(0, 0, 0)

    with tenant_session(tenant_id) as session:  # one REQ-A3 staging transaction
        coverage, coverage_event = _analyze_blob(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            input_ref=input_ref,
            source_map_ref=source_map_ref,
            run_asset_id=None,
            asset_url=None,
        )
    publish(redis, coverage_event)
    log.info(
        "analyze.done",
        run_id=run_id,
        attributed=coverage.attributed,
        unattributed=coverage.unattributed,
        secrets=coverage.secrets,
        secrets_engine=coverage.secrets_engine,
        sources_recovered=coverage.sources_recovered,
        source_map=coverage.source_map,
        findings=coverage.findings_written,
    )
    return coverage


def _analyze_assets(
    redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None,
    rows: list[run_assets.AssetRow],
) -> Coverage:
    """Analyze every fetched-but-not-yet-analyzed asset of a crawl run, best-effort.

    Each asset's findings + ``analyze_status`` commit together in ONE transaction
    (not one loop-wide one) — that per-asset commit is what makes a redelivery
    idempotent (skip an already-analyzed asset rather than re-analyzing it) and
    keeps a mid-loop infra error from rolling back an earlier asset's
    already-committed findings. A control interrupt (REQ-A4) is checked at the
    top of every iteration, before any analyze attempt, and propagates straight
    out of this loop (never caught here — it is not a failure; the broad
    ``except Exception`` below never sees it, since it is raised outside the
    ``try``).

    The ``try`` wraps ONLY the transaction (``_analyze_blob`` + ``set_analyze_ok``)
    — a genuine per-asset analyze failure there is the one thing that should be
    recorded as ``analyze_failed``. ``publish``/logging/``_merge_coverage`` run in
    the paired ``else`` (reached only after a clean commit), NOT in the ``try``:
    if they were in the ``try`` and ``publish`` raised (Redis reset, pool
    exhaustion — the DB itself perfectly healthy), the ``except`` would open a
    fresh transaction and overwrite the just-committed ``"ok"`` with a
    self-contradictory ``"failed"`` on data that was in fact fully analyzed —
    permanently (the row is now analyze-terminal, so redelivery's skip-condition
    never revisits it) and silently (the run would finalize PARTIAL over an
    asset that actually succeeded, with no path back). ``try/except/else`` makes
    that class of post-commit failure propagate instead, out to the worker's
    normal job-level retry, exactly as an infra error should.

    Every asset actually processed (not skipped) gets an unconditional heartbeat
    BEFORE its analyze attempt, regardless of how it turns out — mirrors fetch's
    ``_fetch_assets`` (see its docstring for the full rationale): this bounds the
    max gap between lease renewals to one asset's analyze work, so a run of
    several slow/failing assets can never go unheartbeated long enough for a peer
    worker to reclaim the stream message and double-analyze the remaining assets.
    """
    total = sum(1 for a in rows if a.fetch_status == AssetStatus.OK.value)
    terminal = (AssetStatus.OK.value, AssetStatus.FAILED.value)
    done = 0
    agg = Coverage(0, 0, 0)
    for asset in rows:
        if asset.fetch_status != AssetStatus.OK.value or asset.analyze_status in terminal:
            continue  # not fetched yet, or already analyze-terminal (idempotent skip)
        run_queries.raise_if_control_requested(tenant_id, run_id)  # REQ-A4
        done += 1
        if job_id:
            # Unconditional, once per processed asset — see the docstring above.
            progress.beat(
                redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, done=done, total=total
            )
        try:
            with tenant_session(tenant_id) as session:  # per-asset commit (findings + status)
                coverage, coverage_event = _analyze_blob(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    input_ref=asset.input_ref,
                    source_map_ref=None,  # a crawled asset carries no source map this slice
                    run_asset_id=asset.id,
                    asset_url=asset.url,
                )
                run_assets.set_analyze_ok(session, asset.id)
        except Exception as exc:  # noqa: BLE001 - per-asset best-effort
            with tenant_session(tenant_id) as session:
                run_assets.set_analyze_failed(session, asset.id, str(exc))  # per-asset commit
            log.warning("analyze.asset_failed", run_id=run_id, url=asset.url, error=str(exc))
        else:
            # Only reached if the transaction above committed cleanly. Publish
            # AFTER that commit — REQ-R2 (commit-then-publish, see
            # recon.events.log's module docstring): a subscriber must never
            # observe the event before the finding rows it describes are
            # durably visible to a fresh read. Kept in `else`, NOT in the `try`,
            # so a post-commit failure here (e.g. Redis reset/pool exhaustion —
            # DB perfectly healthy) does not fall into `except` and overwrite
            # the just-committed "ok" with a self-contradictory "failed"; it
            # propagates instead, straight to the worker's job-level retry.
            publish(redis, coverage_event)
            log.info(
                "analyze.asset_done", run_id=run_id, url=asset.url,
                findings=coverage.findings_written,
            )
            agg = _merge_coverage(agg, coverage)
    return agg


def _analyze_blob(
    session,
    *,
    tenant_id: str,
    run_id: str,
    input_ref: str,
    source_map_ref: str | None,
    run_asset_id: str | None,
    asset_url: str | None,
) -> tuple[Coverage, RecordedEvent]:
    """Analyze one blob — the legacy single input OR one crawled asset — and
    persist its findings inside the caller's OPEN ``session``.

    The caller owns the transaction boundary (the legacy path's one REQ-A3
    staging transaction, or Slice Y's per-asset commit) and must ``publish`` the
    returned event only AFTER that transaction commits (REQ-R2) — this function
    deliberately does NOT publish itself. ``run_asset_id``/``asset_url`` are
    threaded into every occurrence (``store.Occurrence``) so the same endpoint
    sighted on two assets still dedupes to one finding with two distinct
    occurrences (Task 2's asset dimension); both are ``None`` for the legacy
    single-blob path, preserving its occurrence hashes exactly as before.
    """
    raw = storage.get_blob(input_ref)
    source = raw.decode("utf-8", "replace")
    # Secret scanning runs out-of-process. A missing binary degrades coverage
    # (status recorded on the event); a genuine engine failure raises here and
    # fails/retries the stage rather than under-reporting secrets.
    scan = kingfisher.scan(raw)

    # Prefer recovered original sources (real per-source paths) when a source map
    # is available; otherwise analyze the bundle under the input.js placeholder.
    # We can't union the two — the source path is part of finding identity, so the
    # same endpoint hashes differently per path (that is why coverage records the
    # map status: map-scoped coverage is NOT full-bundle coverage, REQ-D5).
    units, source_map_status, sources_recovered = _analysis_units(source_map_ref, source)

    attributed = 0
    unattributed = 0
    written = 0
    # Per normalized path (== finding.path), so coverage joins 1:1 to findings and
    # several source names that collapse to one path aggregate together (REQ-C2).
    per_file: dict[str, list[int]] = {}
    for source_name, unit_text in units:
        extraction = extract(unit_text)
        path = normalize.normalize_source_path(source_name)
        attributed += len(extraction.endpoints)
        unattributed += extraction.unattributed
        bucket = per_file.setdefault(path, [0, 0])
        bucket[0] += len(extraction.endpoints)
        bucket[1] += extraction.unattributed
        for endpoint in extraction.endpoints:
            written += _record_endpoint(
                session, tenant_id, run_id, path, source_name, endpoint,
                run_asset_id=run_asset_id, asset_url=asset_url,
            )
    # Secrets are scanned on the original bundle this slice (input.js path).
    # NOTE (follow-up): scanning recovered sources for secrets (real per-source
    # paths for secrets too) is deferred; endpoint/param paths are the D3 win here.
    secret_path = normalize.normalize_source_path(_SOURCE_NAME)
    # Per (rule, snippet) search cursor so N identical secret sightings map to N
    # distinct byte offsets (distinct occurrences, REQ-C2) instead of collapsing.
    secret_cursors: dict[tuple[str, str], int] = {}
    for secret in scan.secrets:
        written += _record_secret(
            session, tenant_id, run_id, secret_path, source, secret, secret_cursors,
            run_asset_id=run_asset_id, asset_url=asset_url,
        )
    files = tuple(
        FileCoverage(path=path, attributed=counts[0], unattributed=counts[1])
        for path, counts in sorted(per_file.items())
    )
    coverage_event = record_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type="analyze.coverage",
        payload={
            "attributed": attributed,
            "unattributed": unattributed,
            "secrets": len(scan.secrets),
            "secrets_engine": scan.status,
            "sources_recovered": sources_recovered,
            "source_map": source_map_status,
            "files": [
                {"path": f.path, "attributed": f.attributed, "unattributed": f.unattributed}
                for f in files
            ],
        },
    )
    coverage = Coverage(
        attributed, unattributed, written,
        secrets=len(scan.secrets), secrets_engine=scan.status,
        sources_recovered=sources_recovered, source_map=source_map_status,
        files=files,
    )
    return coverage, coverage_event


def _merge_coverage(a: Coverage, b: Coverage) -> Coverage:
    """Sum one more asset's Coverage into the loop's running aggregate (Slice Y).

    Only the counts are additive across assets. ``secrets_engine`` reports the
    LESS-healthy of the two statuses ("unavailable" wins over "ok"): taking
    whichever asset merely ran LAST would let a later successful scan mask an
    earlier asset going unscanned, which is exactly the silent-under-reporting
    REQ-C2 exists to prevent (see ``Coverage.secrets_engine``'s own docstring —
    a scanner that was absent must not be reported as "no secrets").
    ``source_map`` is not a health signal the same way (every asset this slice
    passes ``source_map_ref=None``, so it is "none" in practice unless an
    asset's own JS carries an inline map) — the latest asset's value is kept as
    a simple, low-stakes default. ``files`` (per-source-path detail) is already
    durably recorded on each asset's own ``analyze.coverage`` event, which is
    what REQ-C2 reads back (the highest-id event wins — see
    ``findings/queries.py``'s ``_latest_coverage``); this run-level aggregate is
    never persisted, so it stays empty rather than inventing a merge across
    assets' distinct-but-identically-named fallback paths.
    """
    unavailable = "unavailable"
    return Coverage(
        attributed=a.attributed + b.attributed,
        unattributed=a.unattributed + b.unattributed,
        findings_written=a.findings_written + b.findings_written,
        secrets=a.secrets + b.secrets,
        secrets_engine=(
            unavailable if unavailable in (a.secrets_engine, b.secrets_engine) else "ok"
        ),
        sources_recovered=a.sources_recovered + b.sources_recovered,
        source_map=b.source_map,
    )


def _analysis_units(source_map_ref: str | None, source: str) -> tuple[list[tuple[str, str]], str, int]:
    """Decide what to analyze: recovered original sources (real paths) if a source
    map recovers any, else the bundle under ``input.js``. Returns the (name, text)
    units, the source-map status, and the count of recovered files."""
    map_bytes, origin = _resolve_source_map(source_map_ref, source)
    if not map_bytes:
        return [(_SOURCE_NAME, source)], "none", 0

    try:
        recovered = sourcemapper.recover_sources(map_bytes, origin=origin)
    except engines.EngineError:
        # An inline map is opportunistic and rides in the (untrusted) analyzed JS,
        # so a malformed one must NOT fail the run — fall back to bundle analysis.
        # An uploaded map is user-supplied and explicit, so a failure surfaces.
        if origin == "inline":
            return [(_SOURCE_NAME, source)], "inline-error", 0
        raise
    if recovered.status != "ok":  # binary unavailable -> fall back to the bundle
        return [(_SOURCE_NAME, source)], recovered.status, 0
    if not recovered.files:  # map present but nothing recovered (e.g. no sourcesContent)
        return [(_SOURCE_NAME, source)], origin, 0

    units = [(f.path, f.content.decode("utf-8", "replace")) for f in recovered.files]
    return units, origin, len(recovered.files)


def _resolve_source_map(source_map_ref: str | None, source: str) -> tuple[bytes | None, str]:
    if source_map_ref:
        return storage.get_blob(source_map_ref), "uploaded"
    inline = sourcemapper.extract_inline_map(source)
    if inline:
        return inline, "inline"
    return None, "none"


def _record_endpoint(
    session, tenant_id: str, run_id: str, path: str, source_path: str, ep: RawEndpoint,
    *, run_asset_id: str | None = None, asset_url: str | None = None,
) -> int:
    normalized = normalize.normalize_endpoint(ep.method, ep.url)
    written = _write(
        session, tenant_id, run_id, FindingType.ENDPOINT, normalized.value, path,
        occurrence=store.Occurrence(
            host=normalized.host, raw_url=ep.url, source_path=source_path,
            line=ep.line, col=ep.col, offset_start=ep.start_byte, offset_end=ep.end_byte,
            evidence=ep.snippet, engine="vespasian",
            run_asset_id=run_asset_id, asset_url=asset_url,
        ),
        attributes={"kind": ep.kind, "method": ep.method},
    )
    operation = normalize.endpoint_operation(ep.method, ep.url)
    for param in ep.params:
        value = normalize.normalize_param_value(operation, param.location, param.name)
        written += _write(
            session, tenant_id, run_id, FindingType.PARAM, value, path,
            occurrence=store.Occurrence(
                host=normalized.host, raw_url=ep.url, source_path=source_path,
                line=ep.line, col=ep.col, offset_start=ep.start_byte, offset_end=ep.end_byte,
                engine="vespasian",
                run_asset_id=run_asset_id, asset_url=asset_url,
            ),
            attributes={"location": param.location, "name": param.name},
        )
    return written


def _record_secret(
    session, tenant_id: str, run_id: str, path: str, source: str,
    secret: RawSecret, cursors: dict[tuple[str, str], int],
    *, run_asset_id: str | None = None, asset_url: str | None = None,
) -> int:
    # value = provider:sha256(token) — the raw token is never hashed in cleartext.
    value = normalize.normalize_secret_value(secret.snippet, secret.rule_id)
    # REQ-S2 (storage model A): the raw secret is NOT stored — only the identity
    # hash (finding.value) + byte offsets; the plaintext is re-derived just-in-time
    # from the source blob on an audited reveal (recon.probe.reveal), so the platform
    # is never a concentrated store of live credentials. Locate the snippet by
    # CONTENT (kingfisher.locate_snippet), NOT the engine's line/column — those mark
    # the rule match region, which for some rules sits on a different line than the
    # extracted snippet, and a line/column offset would slice the wrong bytes and
    # fail-close the reveal (409). Offsets live in source == raw.decode("utf-8",
    # "replace"), the same byte space reveal slices; the located span always
    # round-trips. A snippet not present verbatim (rare) stores offset-less ->
    # reveal 422, never wrong bytes. line/col stay the engine's (display only).
    key = (secret.rule_id, secret.snippet)
    located = kingfisher.locate_snippet(source, secret.snippet, search_from=cursors.get(key, 0))
    if located is not None:
        offset_start, offset_end = located
        cursors[key] = offset_end  # next identical sighting searches past this one
    else:
        offset_start = offset_end = None
    return _write(
        session, tenant_id, run_id, FindingType.SECRET, value, path,
        occurrence=store.Occurrence(
            source_path=_SOURCE_NAME, line=secret.line, col=secret.column_start,
            offset_start=offset_start, offset_end=offset_end,
            engine="kingfisher", confidence=secret.confidence,
            verified=True if secret.validation_status == "Active" else None,
            run_asset_id=run_asset_id, asset_url=asset_url,
        ),
        attributes={"rule": secret.rule_id, "name": secret.rule_name},
    )


def _write(session, tenant_id, run_id, finding_type, value, path, *, occurrence, attributes) -> int:
    result = store.record_finding(
        session, tenant_id=tenant_id, run_id=run_id, finding_type=finding_type,
        value=value, path=path, occurrence=occurrence, attributes=attributes,
        first_stage="analyzing",
    )
    return int(result.finding_created) + int(result.occurrence_created)
