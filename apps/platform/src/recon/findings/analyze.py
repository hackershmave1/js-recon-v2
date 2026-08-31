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

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError
from redis import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from recon import storage
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import AssetStatus, FindingType
from recon.events.log import RecordedEvent, publish, record_event
from recon.fetch import egress
from recon.findings import (
    _modulegraph,
    deobfuscate,
    engines,
    graphql_ops,
    internal_ip,
    kingfisher,
    normalize,
    queries,
    risk_tags,
    sourcemapper,
    store,
    techdetect_pass,
)
from recon.findings._jsast import _MAX_AST_NODES
from recon.findings.extract import RawEndpoint, extract
from recon.findings.kingfisher import RawSecret
from recon.findings.wrappers import WrapperRule
from recon.observability import get_logger
from recon.progress import heartbeat as progress
from recon.queue import retry
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries

log = get_logger("recon.findings.analyze")

# Fallback source path when no source map recovers the real per-file paths — the
# whole bundle is one logical source. Sourcemapper replaces this with real paths.
_SOURCE_NAME = "input.js"


@dataclass(frozen=True)
class CrossModuleIndex:
    """Run-level index the cross-chunk resolver consults, built once per run by
    `build_export_index`. Two disjoint sub-indexes for the two module systems:

    - ``exports``: ESM — module key (recovered ``f.path`` or no-map ``url_module_key``)
      -> {exported name: string value} (Increments 1 + 2a).
    - ``webpack``: minified webpack — build id (`webpack_build_id`, so ids from two
      different builds in one run never cross-wire) -> module id -> {export: value} (2b).

    Frozen, but the dicts are mutated in place during the build then read-only after.
    """

    exports: dict[str, dict[str, str]] = field(default_factory=dict)
    webpack: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.exports or self.webpack)


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
    # D33-B: opt-in low-confidence "suspected secret" sightings, counted SEPARATELY so
    # the precision-first `secrets` count above is never inflated by the ~50%-FP recall
    # lane. 0 unless the run opted into the `--confidence low` sweep.
    secrets_suspected: int = 0
    # Cleartext info-disclosure sightings (internal-IP literals) — counted SEPARATELY, a
    # NON-secret family, so they never inflate the `secrets` count above.
    internal_ips: int = 0
    # Honest engine status (REQ-C2/§5): a scanner that was absent must not be
    # reported as "no secrets". Reachable values are "ok" and "unavailable" — a
    # genuine engine error/timeout raises before a Coverage is ever returned.
    secrets_engine: str = "ok"
    # Source-map honesty: how many original files were recovered, and how the map
    # was handled (none | uploaded | inline | capture | unavailable | inline-error |
    # capture-error). REQ-D5 must NOT treat map-scoped endpoint coverage as
    # full-bundle coverage.
    sources_recovered: int = 0
    source_map: str = "none"
    # Per-file breakdown of the attributed/unattributed totals (REQ-C2 is a
    # per-file counter — a bundle-wide sum hides which file needs attention).
    files: tuple[FileCoverage, ...] = ()
    # D31 honesty: True when any of this blob's units hit the AST node budget, so the extract
    # bounded its walk to a prefix and the tail was not examined. Surfaced so a partial extract
    # is never silent (REQ-C2); ORed across assets in `_merge_coverage`.
    curtailed: bool = False
    # D37-L2 honesty: True when the source-map recovery hit the cumulative-write budget and stopped
    # short of the whole map (a big-map coverage gap — some recovered originals were NOT scanned).
    # Surfaced so this truncation is never silent (REQ-C2), like `curtailed`; ORed across assets.
    recovered_partial: bool = False


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
    no-op.

    Both branches then flow through a best-effort per-host tech-detection
    fingerprint pass (tech-detection slice, Task 8): it loads the run's
    ``fingerprint-signal`` blob (Tasks 6/7) and upserts ``run_technology``. This
    is enrichment, not a finding — it is swallowed and logged on any failure so
    it can never fail the run (T2), and it never affects the returned
    ``Coverage``."""
    wrappers = _session_wrappers(tenant_id, run_id)  # REQ-D5: recognize taught wrappers live
    # D33-B: the per-run opt-in for the low-confidence "suspected secret" recall lane
    # (nullable column; NULL/false → unchanged medium scan). Read once here and threaded
    # down so every blob of this run scans at the same confidence.
    scan_suspected = _run_scans_suspected(tenant_id, run_id)
    rows = run_assets.list_for_run(tenant_id, run_id)
    if rows:
        coverage = _analyze_assets(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            job_id=job_id,
            rows=rows,
            wrappers=wrappers,
            scan_suspected=scan_suspected,
        )
    else:
        coverage = _analyze_legacy(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            wrappers=wrappers,
            scan_suspected=scan_suspected,
        )
    # Best-effort per-host fingerprint pass (T2): enrichment that must NEVER fail the
    # run (a raise would DLQ -> run FAILED -> all findings lost). A cooperative
    # control interrupt is not a failure, so it propagates.
    try:
        techdetect_pass.run_fingerprint_pass(
            redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id
        )
    except retry.ControlInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment; log, never fail the run
        log.warning("analyze.fingerprint_failed", run_id=run_id, error=str(exc))
    return coverage


def _analyze_legacy(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    wrappers: Sequence[WrapperRule],
    scan_suspected: bool = False,
) -> Coverage:
    """The upload/single-URL path: analyze ``run.input_ref`` as one unit (unchanged)."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        input_ref = run.input_ref if run is not None else None
        source_map_ref = run.source_map_ref if run is not None else None
    if not input_ref:
        return Coverage(0, 0, 0)

    # Cross-chunk pre-pass: a monolithic bundle's map can recover several original
    # modules that import one another (or a webpack bundle registers several modules
    # in one blob), so build the index from this blob too (best-effort — a failure
    # just means no cross-module resolution, never a failed run).
    cross_index = CrossModuleIndex()
    try:
        # asset_url=None: a legacy single blob has no sibling chunk to cross-reference
        # by URL, so only its source-map-recovered modules / in-blob webpack modules contribute.
        _harvest_asset_exports(input_ref, source_map_ref, None, "uploaded", cross_index)
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment
        log.warning("analyze.export_index_legacy_failed", run_id=run_id, error=str(exc))

    with tenant_session(tenant_id) as session:  # one REQ-A3 staging transaction
        coverage, coverage_event = _analyze_blob(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            input_ref=input_ref,
            source_map_ref=source_map_ref,
            run_asset_id=None,
            asset_url=None,
            wrappers=wrappers,
            cross_index=cross_index,
            scan_suspected=scan_suspected,
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


def _session_wrappers(tenant_id: str, run_id: str) -> list[WrapperRule]:
    """Load the taught wrapper callees for the run's session so the analyze stage
    recognizes them live on this and every future run (REQ-D5). Empty when the run
    is invisible (RLS) or has no rules."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return []
        return queries.wrapper_rules_in_session(session, str(run.session_id))


def _run_scans_suspected(tenant_id: str, run_id: str) -> bool:
    """The run's D33-B opt-in: True only if it asked for the low-confidence recall lane.
    A NULL column (every pre-D33-B run + the default) reads False — unchanged behavior."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        return bool(run.scan_suspected_secrets) if run is not None else False


def _analyze_assets(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    job_id: str | None,
    rows: list[run_assets.AssetRow],
    wrappers: Sequence[WrapperRule] = (),
    scan_suspected: bool = False,
) -> Coverage:
    """Analyze every fetched-but-not-yet-analyzed asset of a crawl run, best-effort.

    Each asset's findings + ``analyze_status`` commit together in ONE transaction
    (not one loop-wide one) — that per-asset commit is what makes a redelivery
    idempotent (skip an already-analyzed asset rather than re-analyzing it) and
    keeps a mid-loop infra error from rolling back an earlier asset's
    already-committed findings. A control interrupt (REQ-A4) is checked at the
    top of every iteration, before any analyze attempt, and propagates straight
    out of this loop (never caught here — it is not a failure, and it is raised
    outside the ``try`` below, so neither except clause ever sees it).

    The ``try`` wraps ONLY the transaction (``_analyze_blob`` + ``set_analyze_ok``),
    and its two except clauses split what "failure" means there.
    ``ClientError``/``SQLAlchemyError`` (the blob read or the DB write) are
    INFRASTRUCTURE errors — a transient S3 blip or a DB hiccup, not a verdict on
    this asset's content — so they RE-RAISE to the worker's normal job-level
    retry, matching ``fetch.py``'s narrower
    ``except (EgressBlocked, FatalError, RetryableError)``. Recording one of
    those as ``analyze_failed`` would make a transient blip a PERMANENT per-asset
    failure (the row becomes analyze-terminal, so redelivery's skip-condition
    never revisits it), producing a false PARTIAL for an asset that would have
    succeeded on a retry. Any OTHER exception is a genuine per-asset analyze
    failure (e.g. a malformed unit the extractor chokes on) — that is the one
    thing recorded as ``analyze_failed``, best-effort, so it does not abort the
    rest of the loop.

    ``publish``/logging/``_merge_coverage`` run in the paired ``else`` (reached
    only after a clean commit), NOT in the ``try``: if they were in the ``try``
    and ``publish`` raised (Redis reset, pool exhaustion — the DB itself
    perfectly healthy), the ``except`` would open a fresh transaction and
    overwrite the just-committed ``"ok"`` with a self-contradictory ``"failed"``
    on data that was in fact fully analyzed — permanently (the row is now
    analyze-terminal, so redelivery's skip-condition never revisits it) and
    silently (the run would finalize PARTIAL over an asset that actually
    succeeded, with no path back). ``try/except/else`` makes that class of
    post-commit failure propagate instead, out to the worker's normal job-level
    retry, exactly as an infra error should.

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

    # Phase A (cross-chunk pre-pass): a run-level index of every module's exported
    # string consts so the per-asset loop below can resolve a `fetch(API_BASE +
    # PATH)` whose operands are imported from ANOTHER chunk. Renews the lease +
    # honors a control interrupt per asset (REQ-A4) so this extra pass can't go
    # unheartbeated long enough for a peer worker to reclaim the job.
    def _phase_a_heartbeat() -> None:
        run_queries.raise_if_control_requested(tenant_id, run_id)
        if job_id:
            progress.beat(
                redis,
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                done=0,
                total=total,
                emit_event=False,  # lease renewal only — no progress event during the pre-pass
            )

    def _asset_heartbeat() -> None:
        # Mid-blob lease renewal (D37-L2 S4): recovering + scanning one asset's big source map can
        # take a while, so beat between recovered files so a peer can't reclaim the RUNNING job mid-
        # recovery. Lease-renewal only (emit_event=False) — the per-asset progress event fires once
        # per asset below; `done` is read live so the renewal reflects the asset in flight.
        run_queries.raise_if_control_requested(tenant_id, run_id)
        if job_id:
            progress.beat(
                redis,
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                done=done,
                total=total,
                emit_event=False,
            )

    cross_index = build_export_index(rows, heartbeat=_phase_a_heartbeat)

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
                # An OK-fetched asset always has input_ref: runs/assets.set_fetch_ok writes
                # input_ref + fetch_status=OK atomically, and the loop above only reaches OK
                # assets — so None is unreachable here by invariant (assert, not guard).
                assert asset.input_ref is not None
                coverage, coverage_event = _analyze_blob(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    input_ref=asset.input_ref,
                    # A capture-ingested asset may carry its own source map (the
                    # extension captures the bundle's map post-auth); a crawled
                    # asset's is None. Origin "capture" makes a bad map fall back to
                    # bundle analysis instead of failing the asset (best-effort,
                    # unlike a legacy explicit upload whose failure surfaces).
                    source_map_ref=asset.source_map_ref,
                    source_map_origin="capture",
                    # D32: a referenced .map fetch soft-missed (crawl or capture) →
                    # honest coverage source_map:"skipped" instead of "none".
                    source_map_skipped=asset.source_map_skipped,
                    run_asset_id=asset.id,
                    asset_url=asset.url,
                    wrappers=wrappers,
                    cross_index=cross_index,
                    scan_suspected=scan_suspected,
                    heartbeat=_asset_heartbeat,
                )
                run_assets.set_analyze_ok(session, asset.id)
        except retry.ControlInterrupt:
            # A cooperative pause/cancel (REQ-A4) is NOT a per-asset failure. The per-asset
            # heartbeat (`_asset_heartbeat`) now raises this from DEEP INSIDE `_analyze_blob`
            # (between recovered files, D37-L2 S4) — i.e. inside this `try` — so it would
            # otherwise be swallowed by the `except Exception` below and mark a paused asset
            # permanently `analyze_failed` (terminal → the resume skip never revisits it →
            # silent loss). Re-raise so it propagates out of the loop exactly as the pre-loop
            # control check does, matching the fingerprint pass's `except ControlInterrupt: raise`.
            raise
        except (ClientError, SQLAlchemyError):
            raise  # infra (storage/DB) -> job-level retry, matching fetch.py
        except Exception as exc:
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
                "analyze.asset_done",
                run_id=run_id,
                url=asset.url,
                findings=coverage.findings_written,
            )
            agg = _merge_coverage(agg, coverage)
    return agg


@dataclass(frozen=True)
class _EndpointExtraction:
    """Endpoint-loop result for one blob (no secrets, no coverage event). The
    coverage counters ride along so `_analyze_blob` can still build its
    `analyze.coverage` payload; the out-of-band re-extract ignores them."""

    written: int
    attributed: int
    unattributed: int
    sources_recovered: int
    source_map: str
    files: tuple[FileCoverage, ...]
    # D31: True if any unit's extract() hit the node budget (surfaced on coverage; also read by
    # the re-extract path to log its own tail-drop). Defaulted so older constructions stay valid.
    curtailed: bool = False


def _resolve_cross_module(
    importer_key: str | None, unit_text: str, cross_index: CrossModuleIndex | None
) -> tuple[dict[str, str] | None, dict[str, dict[str, str]] | None]:
    """Resolve this unit's cross-chunk references, returning
    ``(cross_module_consts, webpack_members)`` for `extract()` — either/both ``None``
    when nothing resolves (keeping `extract()` on its unchanged per-file path).

    ESM: a unit's named imports resolved against the export index, keyed by
    ``importer_key`` (recovered ``f.path`` or a no-map chunk's ``url_module_key``).
    Webpack: a unit's ``require`` aliases resolved against its OWN build's modules
    (`webpack_build_id`), so ids from a different build never cross-wire. Both are
    import/require-filtered upstream, so an unrelated module can never leak a value
    in and fabricate a URL (REQ-C2). The unit is parsed once here for both."""
    if not cross_index:
        return None, None
    tree = _modulegraph.parse(unit_text)
    consts: dict[str, str] | None = None
    if cross_index.exports and importer_key is not None:
        imports = _modulegraph.collect_named_imports(tree)
        if imports:
            consts = (
                _modulegraph.build_cross_module_consts(importer_key, imports, cross_index.exports)
                or None
            )
    members: dict[str, dict[str, str]] | None = None
    if cross_index.webpack:
        build_id = _modulegraph.webpack_build_id(unit_text)
        modules = cross_index.webpack.get(build_id) if build_id is not None else None
        if modules:
            requires = _modulegraph.collect_webpack_requires(tree)
            resolved = {a: modules[m] for a, m in requires.items() if m in modules}
            members = resolved or None
    return consts, members


def _extract_endpoints(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    units: AnalysisUnits,
    run_asset_id: str | None,
    asset_url: str | None,
    wrappers: Sequence[WrapperRule] = (),
    cross_index: CrossModuleIndex | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> _EndpointExtraction:
    """Extract + record ONLY endpoint/param findings for one blob's ``units``.

    Shared core of the analyze stage (`_analyze_blob`, which additionally scans secrets + emits
    coverage) and the out-of-band wrapper re-extract (`recon.findings.reextract`, which calls this
    directly so a wrapper POST records findings WITHOUT re-emitting the run's coverage counters —
    spec §2.6/§12 Blocker 1 — and WITHOUT the Kingfisher subprocess — §12 Blocker 2). Both callers
    build the SAME ``AnalysisUnits`` so a re-emitted native endpoint keeps its source-map-recovered
    path and thus its stable `finding_hash` (§12 Imp 4).

    Reads each recovered unit's text ONE AT A TIME from the on-disk beautified tree (D37-L2 slice 3)
    and beats before each (S4) so a big map's per-file tree-sitter extract stays lease-safe.
    ``cross_index`` is the run-level cross-module index (`build_export_index`); when supplied, each
    unit's ESM imports / webpack requires are resolved against it so a cross-chunk
    `fetch(API_BASE + PATH)` / `fetch(r.t + r.M)` attributes. BOTH callers pass the SAME index so the
    re-extract writes the identical resolved endpoint the analyze pass did — never a contradictory
    unresolved skeleton."""
    attributed = 0
    unattributed = 0
    written = 0
    # D31: any unit hitting the node budget makes the whole blob's extract partial.
    curtailed = False
    per_file: dict[str, list[int]] = {}
    # A recovered asset yields >=1 units keyed by `f.path`; a no-map asset yields the single
    # "input.js" bundle unit whose module identity is the served URL, not a per-module path.
    is_bundle = units.is_bundle
    for source_name in units.names:
        if heartbeat is not None:
            heartbeat()  # lease renew + REQ-A4 control-check, before each unit's extract (S4)
        unit_text = units.read_text(source_name)
        importer_key = (
            _modulegraph.url_module_key(asset_url)
            if is_bundle and asset_url is not None
            else source_name
        )
        # cross-module refs resolve against the index under that same key (recovered
        # `f.path`, or no-map `url_module_key`) for ESM, and per-build for webpack.
        cross_module_consts, webpack_members = _resolve_cross_module(
            importer_key, unit_text, cross_index
        )
        extraction = extract(
            unit_text,
            wrappers=wrappers,
            cross_module_consts=cross_module_consts,
            webpack_members=webpack_members,
        )
        path = normalize.normalize_source_path(source_name)
        attributed += len(extraction.endpoints)
        unattributed += extraction.unattributed
        curtailed = curtailed or extraction.curtailed
        bucket = per_file.setdefault(path, [0, 0])
        bucket[0] += len(extraction.endpoints)
        bucket[1] += extraction.unattributed
        for endpoint in extraction.endpoints:
            written += _record_endpoint(
                session,
                tenant_id,
                run_id,
                path,
                source_name,
                endpoint,
                run_asset_id=run_asset_id,
                asset_url=asset_url,
            )
        # Tier 4 (unconfirmed lane): surface the SAME unresolved sinks already counted
        # in `unattributed` above, as a distinct ENDPOINT_UNRESOLVED finding. Deliberately
        # OUTSIDE the attributed/unattributed accounting (REQ-C2 honesty is unchanged) and,
        # being a distinct type, excluded from every `type == 'endpoint'` read model.
        for unresolved in extraction.unresolved:
            written += _record_unresolved_endpoint(
                session,
                tenant_id,
                run_id,
                path,
                source_name,
                unresolved,
                run_asset_id=run_asset_id,
                asset_url=asset_url,
            )
        # Tier 5 (generic-call): a SUSPECTED sink — a verb call on an unrecognised HTTP-client-
        # shaped receiver. A distinct ENDPOINT_GENERIC type (auto-excluded from every
        # type=='endpoint' read model, same as Tier 4) and, being only suspected rather than
        # detected, deliberately OUTSIDE the attributed/unattributed accounting — it must never
        # move the REQ-C2 coverage counters.
        for generic in extraction.generic:
            written += _record_unresolved_endpoint(
                session,
                tenant_id,
                run_id,
                path,
                source_name,
                generic,
                run_asset_id=run_asset_id,
                asset_url=asset_url,
                finding_type=FindingType.ENDPOINT_GENERIC,
            )
        # Page routes (Phase 2): client-side navigation targets. A DISTINCT PAGE_ROUTE type
        # (auto-excluded from every type=='endpoint' read model) and, like the generic lane,
        # deliberately OUTSIDE the attributed/unattributed accounting — a referenced route is
        # not a detected backend sink, so it must never move the REQ-C2 coverage counters.
        for route in extraction.routes:
            written += _record_unresolved_endpoint(
                session,
                tenant_id,
                run_id,
                path,
                source_name,
                route,
                run_asset_id=run_asset_id,
                asset_url=asset_url,
                finding_type=FindingType.PAGE_ROUTE,
            )
    files = tuple(
        FileCoverage(path=path, attributed=counts[0], unattributed=counts[1])
        for path, counts in sorted(per_file.items())
    )
    return _EndpointExtraction(
        written=written,
        attributed=attributed,
        unattributed=unattributed,
        sources_recovered=units.sources_recovered,
        source_map=units.source_map_status,
        files=files,
        curtailed=curtailed,
    )


def _analyze_blob(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    input_ref: str,
    source_map_ref: str | None,
    source_map_origin: str = "uploaded",
    source_map_skipped: bool = False,
    run_asset_id: str | None,
    asset_url: str | None,
    wrappers: Sequence[WrapperRule] = (),
    cross_index: CrossModuleIndex | None = None,
    scan_suspected: bool = False,
    heartbeat: Callable[[], None] | None = None,
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
    # D33-B: when the run opted in, scan at `--confidence low` so the recall lane's
    # rules (and low-confidence built-ins) emit; each sighting is then partitioned by
    # confidence into SECRET (medium/high) vs SECRET_SUSPECTED (low) below. Not opted in
    # → default medium scan, all SECRET (unchanged).
    confidence = "low" if scan_suspected else None
    scan = kingfisher.scan(raw, confidence=confidence)

    # The recovered-source tree (if any) lives on disk under `units` for this block; the `with` frees
    # it once endpoints, recovered secrets, and recovered internal-IPs are recorded (D37-L2 slice 3).
    # A no-map bundle holds its single unit in RAM (no temp dir). `heartbeat` renews the worker lease
    # between recovered files so a big map's per-file work stays lease-safe (S4). The recovered passes
    # run FIRST (then the raw-bundle passes below); order is immaterial — each records into a distinct
    # source_path/byte-space and the transactional-outbox upserts are idempotent.
    with _analysis_units(
        source_map_ref, source, source_map_origin, source_map_skipped, heartbeat=heartbeat
    ) as units:
        endpoints = _extract_endpoints(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            units=units,
            run_asset_id=run_asset_id,
            asset_url=asset_url,
            wrappers=wrappers,
            cross_index=cross_index,
            heartbeat=heartbeat,
        )
        recovered_written, recovered_secrets, recovered_suspected = _record_recovered_secrets(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            units=units,
            run_asset_id=run_asset_id,
            asset_url=asset_url,
            confidence=confidence,
            heartbeat=heartbeat,
        )
        recovered_ip_written, recovered_ips = _record_recovered_internal_ips(
            session,
            units,
            tenant_id=tenant_id,
            run_id=run_id,
            run_asset_id=run_asset_id,
            asset_url=asset_url,
            heartbeat=heartbeat,
        )
        # Capture the honest partial flag before the tree (and `units`) is freed at the `with` exit.
        recovered_partial = units.partial
    written = endpoints.written + recovered_written + recovered_ip_written
    # D31 honesty: the node budget bounded this blob's extract to a prefix — never silent. Surfaced
    # durably on the coverage event/read model below; this operator log mirrors the convention of
    # the other soft-miss signals (`analyze.asset_failed`, `fetch.source_map_skipped`).
    if endpoints.curtailed:
        log.warning(
            "analyze.extract_curtailed",
            run_id=run_id,
            asset_url=asset_url,
            cap=_MAX_AST_NODES,
        )

    # Secrets are scanned on the raw minified bundle (this loop, source_path "input.js")
    # AND on each source-map-recovered original (D32-B1, in the `with` above). The bundle scan
    # is the floor — everything a minified string literal exposes; the recovered scan adds what
    # lives ONLY in the original (a secret in a comment, or in code tree-shaken out of the
    # bundle) — the silent gap D32 closes. A token in both is 1 finding / 2 occurrences.
    secret_path = normalize.normalize_source_path(_SOURCE_NAME)
    # Per (rule, snippet) search cursor so N identical secret sightings map to N
    # distinct byte offsets (distinct occurrences, REQ-C2) instead of collapsing.
    secret_cursors: dict[tuple[str, str], int] = {}
    # Seed the SIGHTING counts with the recovered lane's (REQ-C2 honesty: counts are sightings, so a
    # recovered-only secret is never under-reported — the D32 silent-miss this closes — and a token
    # in both counts twice). D33-B: the precision-first `secrets` count excludes the low-confidence
    # lane, which keeps its own `secrets_suspected` count.
    secrets_sighted = recovered_secrets
    suspected_sighted = recovered_suspected
    for secret in scan.secrets:
        finding_type = _secret_finding_type(secret)
        written += _record_secret(
            session,
            tenant_id,
            run_id,
            secret_path,
            source,
            secret,
            secret_cursors,
            finding_type=finding_type,
            run_asset_id=run_asset_id,
            asset_url=asset_url,
        )
        if finding_type is FindingType.SECRET_SUSPECTED:
            suspected_sighted += 1
        else:
            secrets_sighted += 1
    # Cleartext internal-IP literals (info-disclosure, NOT secrets): the raw bundle here + each
    # recovered original in the `with` above — a token in both is 1 finding / 2 occurrences. Unlike a
    # secret, the value is stored/shown CLEARTEXT (never hashed/redacted/revealable) and counted in
    # its OWN `internal_ips` total, so the ~precision `secrets` count is never inflated.
    internal_ips_sighted = recovered_ips
    for sighting in internal_ip.find_internal_ips(source):
        written += _record_internal_ip(
            session,
            tenant_id,
            run_id,
            secret_path,
            _SOURCE_NAME,
            sighting,
            run_asset_id=run_asset_id,
            asset_url=asset_url,
        )
        internal_ips_sighted += 1
    # GraphQL operations (enrichment C, export-only): a run-level artifact, never a
    # finding — persisted separately so it never pollutes the HTTP-endpoints read model.
    _record_graphql_operations(
        session, tenant_id=tenant_id, run_id=run_id, source=source, asset_url=asset_url
    )
    coverage_event = record_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type="analyze.coverage",
        payload={
            "attributed": endpoints.attributed,
            "unattributed": endpoints.unattributed,
            "secrets": secrets_sighted,
            "secrets_suspected": suspected_sighted,
            "internal_ips": internal_ips_sighted,
            "secrets_engine": scan.status,
            "sources_recovered": endpoints.sources_recovered,
            "source_map": endpoints.source_map,
            "curtailed": endpoints.curtailed,
            "recovered_partial": recovered_partial,
            "files": [
                {"path": f.path, "attributed": f.attributed, "unattributed": f.unattributed}
                for f in endpoints.files
            ],
        },
    )
    coverage = Coverage(
        endpoints.attributed,
        endpoints.unattributed,
        written,
        secrets=secrets_sighted,
        secrets_suspected=suspected_sighted,
        internal_ips=internal_ips_sighted,
        secrets_engine=scan.status,
        sources_recovered=endpoints.sources_recovered,
        source_map=endpoints.source_map,
        files=endpoints.files,
        curtailed=endpoints.curtailed,
        recovered_partial=recovered_partial,
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
    ``source_map`` is not a health signal the same way (a crawl asset passes
    ``source_map_ref=None`` so it is "none" unless its JS carries an inline map; a
    capture asset may carry its own uploaded map → "capture"/"capture-error") — the
    latest asset's value is kept EXCEPT a D32 "skipped" (a referenced map we could not retrieve)
    dominates, so an honest coverage gap survives the merge. ``files`` (per-source-path detail) is already
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
        secrets_suspected=a.secrets_suspected + b.secrets_suspected,  # D33-B: additive like secrets
        internal_ips=a.internal_ips + b.internal_ips,  # cleartext info-disclosure: additive too
        secrets_engine=(
            unavailable if unavailable in (a.secrets_engine, b.secrets_engine) else "ok"
        ),
        sources_recovered=a.sources_recovered + b.sources_recovered,
        # D32: a "skipped" (referenced map we couldn't retrieve) DOMINATES so a non-last
        # skipped asset is never masked by the latest asset's value (mirrors curtailed's OR
        # and the payload-merge below); otherwise the latest asset's value is kept.
        source_map=("skipped" if "skipped" in (a.source_map, b.source_map) else b.source_map),
        curtailed=a.curtailed or b.curtailed,
        recovered_partial=a.recovered_partial
        or b.recovered_partial,  # any truncated recovery survives
    )


class AnalysisUnits:
    """What analyze scans for one blob: either the single in-RAM bundle unit (no usable map) or a
    source-map-recovered, BEAUTIFIED tree materialized ON DISK (D37-L2 slice 3), read one file at a
    time so a 96 MiB map is never held whole in RAM.

    A CONTEXT MANAGER: ``__exit__`` removes the on-disk beautified tree (a no-op for the bundle
    case), so the caller must ``with`` it. ``source_map_status`` / ``sources_recovered`` carry the
    same honesty signals the old ``(units, status, count)`` tuple did (``is_bundle`` ==
    ``sources_recovered == 0``). ``partial`` is True when the cumulative-write budget stopped
    recovery short of the whole map (an honest coverage gap, REQ-C2)."""

    def __init__(
        self,
        *,
        source_map_status: str,
        sources_recovered: int,
        names: list[str],
        tree_root: str | None,
        bundle_text: str | None,
        tree_bytes: int = 0,
        partial: bool = False,
        tmp: tempfile.TemporaryDirectory[str] | None = None,
    ) -> None:
        self.source_map_status = source_map_status
        self.sources_recovered = sources_recovered
        self.names = names
        self.tree_root = tree_root
        self.tree_bytes = tree_bytes
        self.partial = partial
        self._bundle_text = bundle_text
        self._tmp = tmp

    @property
    def is_bundle(self) -> bool:
        # 0 recovered == the lone "input.js" bundle unit; route on this explicit signal, not the
        # sentinel string (a map's author-controlled sources[] could name a real file "input.js").
        return self.sources_recovered == 0

    def read_text(self, name: str) -> str:
        """The unit text for ``name`` — the single bundle text, or ONE recovered file read back
        from the on-disk beautified tree (utf-8; the bytes were written as ``text.encode("utf-8")``,
        so this reproduces the exact text ``recon.probe.sources.recover_file_text`` beautifies at
        reveal — the byte-exact both-sides invariant, D37-L2 M2)."""
        if self.tree_root is None:
            return self._bundle_text or ""
        with open(os.path.join(self.tree_root, *name.split("/")), "rb") as handle:
            return handle.read().decode("utf-8")

    def __enter__(self) -> AnalysisUnits:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()


def _beautify_recovered_to_disk(
    map_path: str, tree_root: str, *, heartbeat: Callable[[], None] | None = None
) -> tuple[list[str], int, bool]:
    """Stream each recovered original from the map at ``map_path`` -> beautify it -> write
    ``text.encode("utf-8")`` byte-exact to ``<tree_root>/<rel>`` (M2: binary, no added newline/BOM),
    in the generator's stable order (REQ-A3). Returns ``(names, total_bytes, partial)``.

    Beautify runs ``beautify_if_minified`` — the SAME function ``recon.probe.sources`` /
    ``reveal`` reproduce, so the scanned/located bytes equal the reveal byte space (a minified
    vendor original is pretty-printed; a real multi-line original passes through). The cumulative-
    write budget stops at WHOLE-FILE granularity: a file that would cross it is skipped and recovery
    stops (``partial=True``, honest gap; never a mid-file cut, which would desync reveal -> 409).
    Beats before each file (S4) so one giant file's beautify+write can't outlast the 30s stall
    window. Raises ``EngineNotAvailable``/``EngineError`` from
    :func:`sourcemapper.iter_recovered_files` for the caller's fallback."""
    settings = get_settings()
    # The raw recovered tree is <= the map's sourcesContent, bounded by the input cap; beautify can
    # EXPAND a minified original, so the on-disk budget is 2x that cap — headroom for expansion,
    # still bounded so a pathological all-minified map trips an honest partial rather than filling
    # the disk. NOT prlimit --fsize (RLIMIT_FSIZE is per-FILE, not cumulative — D37-L2 S1).
    recovered_cap = settings.max_source_map_bytes
    write_budget = 2 * settings.max_source_map_bytes
    root_real = os.path.realpath(tree_root)
    names: list[str] = []
    total = 0
    partial = False
    # The generator owns the sourcemapper -output dir; it is finalized (dir cleaned) when this
    # function returns and `files` goes out of scope — including an early budget break.
    files = sourcemapper.iter_recovered_files(map_path, max_recovered_bytes=recovered_cap)
    for rel, raw in files:
        if heartbeat is not None:
            heartbeat()  # lease renew + REQ-A4 control-check, before each file (S4)
        text = deobfuscate.beautify_if_minified(raw.decode("utf-8", "replace"))
        encoded = text.encode("utf-8")
        if total + len(encoded) > write_budget:
            partial = True
            log.warning("analyze.recovered_tree_truncated", budget=write_budget, written=total)
            break
        dest = os.path.join(tree_root, *rel.split("/"))
        real = os.path.realpath(dest)
        # Containment on WRITE (defense-in-depth, mirrors sourcemapper._read_recovered_file): a
        # recovered `rel` that would escape the tree root is dropped, never written.
        if real != root_real and not real.startswith(root_real + os.sep):
            log.warning("analyze.recovered_escaped_path", path=rel)
            continue
        os.makedirs(os.path.dirname(dest) or tree_root, exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(encoded)
        names.append(rel)
        total += len(encoded)
    return names, total, partial


def _analysis_units(
    source_map_ref: str | None,
    source: str,
    source_map_origin: str = "uploaded",
    source_map_skipped: bool = False,
    *,
    heartbeat: Callable[[], None] | None = None,
) -> AnalysisUnits:
    """Decide what to analyze: source-map-recovered originals (real paths) if a map recovers any,
    else the whole bundle under ``input.js``. Returns an :class:`AnalysisUnits` — a CONTEXT MANAGER
    the caller must ``with`` so the on-disk beautified tree (recovered case) is cleaned up.

    D37-L2 slice 3: the recovered originals are STREAMED (``sourcemapper.iter_recovered_files``),
    beautified byte-exact, and written to an on-disk tree read one file at a time — so a 96 MiB map
    is recovered whole (endpoints) without ever holding the tree in RAM (the old 32 MiB in-RAM cap).
    The map itself is streamed to a temp file (never ``get_blob`` whole)."""

    def _bundle(status: str) -> AnalysisUnits:
        # The whole bundle as one endpoint unit under input.js, beautified (deterministic,
        # cap-guarded) so findings get distinct line numbers and recon.probe.sources serves the
        # same beautify; raw when beautify is over-cap/unavailable. Secrets scan the raw bytes in
        # _analyze_blob (recon.probe.reveal slices raw offsets), unaffected.
        beautified = deobfuscate.beautify(source)
        return AnalysisUnits(
            source_map_status=status,
            sources_recovered=0,
            names=[_SOURCE_NAME],
            tree_root=None,
            bundle_text=beautified if beautified is not None else source,
        )

    if source_map_ref:
        origin = source_map_origin
        inline_bytes: bytes | None = None
    else:
        # D32: a REFERENCED map the fetch stage couldn't retrieve (``source_map_skipped``) is an
        # honest "skipped" gap — distinct from a bundle that had no map ("none"). Both fall back to
        # bundle analysis; only the label differs (the Overview surfaces a "Partial" banner, never
        # silent). NOTE(DEBT D32): a future run-to-run diff MUST treat a "skipped" asset's absent
        # recovered-source findings as UNKNOWN, not REMOVED — we never looked, the source didn't
        # vanish. Absent a stored ref, an inline data: map is opportunistic.
        inline_bytes = sourcemapper.extract_inline_map(source)
        if inline_bytes is None:
            return _bundle("skipped" if source_map_skipped else "none")
        origin = "inline"

    tmp = tempfile.TemporaryDirectory(prefix="sm-beaut-")
    ok = False
    try:
        with tempfile.TemporaryDirectory(prefix="smmap-") as map_workdir:
            map_path = os.path.join(map_workdir, "in.map")
            if source_map_ref:
                # Stream the map to the temp file — never get_blob it whole into RAM.
                storage.download_blob_to_path(source_map_ref, map_path)
            else:
                assert inline_bytes is not None  # set in the else-branch above
                with open(map_path, "wb") as handle:
                    handle.write(inline_bytes)
            names, tree_bytes, partial = _beautify_recovered_to_disk(
                map_path, tmp.name, heartbeat=heartbeat
            )
        if not names:  # map present but nothing recovered (no sourcesContent, or all dropped)
            return _bundle(origin)
        ok = True
        return AnalysisUnits(
            source_map_status=origin,
            sources_recovered=len(names),
            names=names,
            tree_root=tmp.name,
            bundle_text=None,
            tree_bytes=tree_bytes,
            partial=partial,
            tmp=tmp,
        )
    except engines.EngineNotAvailable:
        # binary unavailable -> fall back to the bundle (matches the old recovered.status guard).
        return _bundle("unavailable")
    except engines.EngineError:
        # An inline/capture map is opportunistic — a malformed one must NOT fail the run/asset:
        # fall back to bundle analysis with the honest "<origin>-error" status. A legacy uploaded
        # map is a deliberate user upload, so its failure surfaces (re-raise).
        if origin in ("inline", "capture"):
            return _bundle(f"{origin}-error")
        raise
    finally:
        # Every exit that does NOT hand `tmp` to a returned AnalysisUnits (bundle fallback, an
        # error, a control interrupt from the heartbeat) frees the tree here.
        if not ok:
            tmp.cleanup()


def _resolve_source_map(
    source_map_ref: str | None, source: str, source_map_origin: str = "uploaded"
) -> tuple[bytes | None, str]:
    # A stored ref is the explicit map for this unit; its origin ("uploaded" legacy,
    # "capture" from the extension) decides whether a parse failure surfaces or falls
    # back (see _analysis_units). Absent a ref, an inline data: map is opportunistic.
    if source_map_ref:
        return storage.get_blob(source_map_ref), source_map_origin
    inline = sourcemapper.extract_inline_map(source)
    if inline:
        return inline, "inline"
    return None, "none"


def _merge_module_exports(exports: dict[str, dict[str, str]], key: str, content: bytes) -> None:
    module_exports = _modulegraph.collect_module_exports(_modulegraph.parse(content))
    if module_exports:
        exports.setdefault(key, {}).update(module_exports)


def _harvest_minified(source: str, asset_url: str | None, index: CrossModuleIndex) -> None:
    """No-map chunk: parse the minified source ONCE and harvest BOTH webpack modules
    (keyed per build id, `webpack_build_id`) and minified-ESM exports (keyed by URL
    path). A chunk is one bundler or the other, so the wrong-bundler branch simply
    finds nothing — no double parse (folds the webpack harvest into the ESM pass, F6)."""
    tree = _modulegraph.parse(source)
    build_id = _modulegraph.webpack_build_id(source)
    if build_id is not None:
        modules = _modulegraph.collect_webpack_modules(tree)
        if modules:
            index.webpack.setdefault(build_id, {}).update(modules)
    if asset_url:
        esm_exports = _modulegraph.collect_module_exports(tree)
        if esm_exports:
            index.exports.setdefault(_modulegraph.url_module_key(asset_url), {}).update(esm_exports)


def _harvest_asset_exports(
    input_ref: str,
    source_map_ref: str | None,
    asset_url: str | None,
    source_map_origin: str,
    index: CrossModuleIndex,
) -> None:
    """Merge one asset's cross-module exports into ``index``, keyed to MIRROR the
    module identity the per-asset extract loop will use (`_analysis_units`):

    - a source map that recovers original files -> ESM exports keyed by each recovered
      ``f.path``;
    - otherwise (no map, unavailable, or nothing recovered -> the loop analyzes the
      asset as one ``input.js`` bundle unit) -> `_harvest_minified`: webpack modules
      keyed per build id, and minified-ESM `export{local as Name}` keyed by URL path.
    """
    if source_map_ref:
        # `origin` (the map-provenance label) only ever fed `recover_sources`' return value, which
        # this harvest never read — dropped now that recovery streams (D37-L2 slice 5).
        map_bytes, _origin = _resolve_source_map(source_map_ref, "", source_map_origin)
        source: str | None = None
    else:  # no stored ref -> read the blob (an inline `data:` map may still contribute)
        source = storage.get_blob(input_ref).decode("utf-8", "replace")
        map_bytes, _origin = _resolve_source_map(None, source, source_map_origin)
    if map_bytes:
        recovered_any = False
        try:
            with tempfile.TemporaryDirectory(prefix="sm-idx-") as workdir:
                map_path = os.path.join(workdir, "in.map")
                with open(map_path, "wb") as handle:
                    handle.write(map_bytes)
                # D37-L2 slice 5: STREAM the recovery one file at a time at the SAME cap the per-asset
                # loop uses (max_source_map_bytes = 96 MiB), so this pre-pass harvests exports from
                # EVERY file the loop will extract — closing the 32-vs-96 MiB divergence (slice-3 §4
                # review) — WITHOUT holding the whole recovered tree in RAM. Exports parse from raw
                # bytes, in the same stable order recover_sources materialized, so the index is
                # unchanged (only the tail beyond 32 MiB is now covered). NOTE(DEBT D28): this still
                # recovers the map a SECOND time (the loop re-recovers it for extraction); a
                # recover-once reuse cache is its own tracked slice.
                for rel_path, raw in sourcemapper.iter_recovered_files(
                    map_path, max_recovered_bytes=get_settings().max_source_map_bytes
                ):
                    recovered_any = True
                    _merge_module_exports(index.exports, rel_path, raw)
        except engines.EngineError:
            # Malformed map, or the binary is absent (EngineNotAvailable subclasses EngineError):
            # `_analysis_units` falls back to BUNDLE analysis for an inline/capture map (so the loop
            # keys this asset by its URL — populate that below); an uploaded/legacy map has no
            # asset_url, so the URL branch no-ops and the extract loop fails the asset anyway. Either
            # way, fall through. (The sourcemapper subprocess fails all-or-nothing BEFORE any file is
            # yielded, so no partial index is left behind.)
            recovered_any = False
        if recovered_any:
            return  # recovered -> rel_path ESM keys (matches _analysis_units's recovered branch)
    # No usable map: the loop analyzes this asset as one bundle unit. Harvest webpack
    # modules (by build id — cross-chunk sibling) and/or minified-ESM exports (by URL).
    if source is None:
        source = storage.get_blob(input_ref).decode("utf-8", "replace")
    _harvest_minified(source, asset_url, index)


def build_export_index(
    rows: Sequence[run_assets.AssetRow],
    *,
    source_map_origin: str = "capture",
    heartbeat: Callable[[], None] | None = None,
) -> CrossModuleIndex:
    """Run-level `CrossModuleIndex` so the per-asset extract loop can resolve a
    cross-chunk `fetch(API_BASE + PATH)` (ESM) or `fetch(r.t + r.M)` (webpack) whose
    operands live in a sibling chunk (recon.findings._modulegraph).

    Best-effort enrichment, exactly like the fingerprint pass: a per-asset failure
    contributes nothing and NEVER fails the run; only a cooperative control interrupt
    (raised by ``heartbeat``) propagates. ``heartbeat`` is called once per processed
    asset to renew the worker lease + honor REQ-A4 (the crawl-analyze caller supplies
    it; the synchronous re-extract caller passes ``None``).

    Covers the source-map path (ESM exports by ``f.path``), the no-map minified-ESM
    path (ESM exports by ``url_module_key``), and the no-map webpack path (modules per
    build id) — see ``_harvest_asset_exports`` / ``_harvest_minified``.

    NOTE(DEBT D28): for a mapped asset this recovers the source map a SECOND time —
    the per-asset extract loop recovers it again for full extraction, so a large crawl
    pays 2x sourcemapper subprocess spawns per mapped asset; a no-map asset is likewise
    tree-sitter-parsed here AND again in the loop. Correct and memory-bounded (only the
    small index persists, not recovered/source text). D37-L2 slice 5 STREAMED this harvest
    (``_harvest_asset_exports`` now iterates ``iter_recovered_files`` at the loop's 96 MiB cap —
    closing the 32-vs-96 MiB divergence the slice-3 §4 review found, so cross-chunk refs into the
    >32 MiB tail now resolve) but deliberately did NOT remove the double-recover: a recover-once
    reuse cache spanning this pre-pass and the loop is entangled (bounded-disk cache + tree-ownership
    across the Phase-A/loop boundary), perf-only, and stays tracked as D28 for its own slice.
    """
    index = CrossModuleIndex()
    for asset in rows:
        if asset.fetch_status != AssetStatus.OK.value or not asset.input_ref:
            continue
        if heartbeat is not None:
            heartbeat()  # lease renew + REQ-A4 control-check (may raise ControlInterrupt)
        try:
            _harvest_asset_exports(
                asset.input_ref, asset.source_map_ref, asset.url, source_map_origin, index
            )
        except Exception as exc:  # noqa: BLE001 - best-effort; a bad asset just yields nothing
            log.warning("analyze.export_index_asset_failed", url=asset.url, error=str(exc))
    return index


def _record_endpoint(
    session: Session,
    tenant_id: str,
    run_id: str,
    path: str,
    source_path: str,
    ep: RawEndpoint,
    *,
    run_asset_id: str | None = None,
    asset_url: str | None = None,
) -> int:
    normalized = normalize.normalize_endpoint(ep.method, ep.url)
    endpoint_attributes: dict[str, Any] = {"kind": ep.kind, "method": ep.method}
    if ep.wrapper:
        endpoint_attributes["wrapper"] = ep.wrapper
    if ep.headers:
        # Auth surface captured statically (enrichment B): header names + scheme keyword,
        # never a credential value. Non-identity attribute -> no finding_hash churn.
        endpoint_attributes["auth"] = [{"name": h.name, "scheme": h.scheme} for h in ep.headers]
    written = _write(
        session,
        tenant_id,
        run_id,
        FindingType.ENDPOINT,
        normalized.value,
        path,
        occurrence=store.Occurrence(
            host=normalized.host,
            raw_url=ep.url,
            source_path=source_path,
            line=ep.line,
            col=ep.col,
            offset_start=ep.start_byte,
            offset_end=ep.end_byte,
            evidence=ep.snippet,
            engine="vespasian",
            run_asset_id=run_asset_id,
            asset_url=asset_url,
        ),
        attributes=endpoint_attributes,
    )
    operation = normalize.endpoint_operation(ep.method, ep.url)
    for param in ep.params:
        value = normalize.normalize_param_value(operation, param.location, param.name)
        param_attributes: dict[str, Any] = {"location": param.location, "name": param.name}
        tags = risk_tags.classify_param(param.name)
        if tags:
            # Advisory name-based risk tags (auth/admin/idor/flag). attributes is display-only
            # (NOT part of finding_hash), so this never churns finding identity and rides the
            # existing passthrough to GET /runs/{id}/findings with no router change.
            param_attributes["risk_tags"] = list(tags)
        written += _write(
            session,
            tenant_id,
            run_id,
            FindingType.PARAM,
            value,
            path,
            occurrence=store.Occurrence(
                host=normalized.host,
                raw_url=ep.url,
                source_path=source_path,
                line=ep.line,
                col=ep.col,
                offset_start=ep.start_byte,
                offset_end=ep.end_byte,
                engine="vespasian",
                run_asset_id=run_asset_id,
                asset_url=asset_url,
            ),
            attributes=param_attributes,
        )
    return written


def _record_unresolved_endpoint(
    session: Session,
    tenant_id: str,
    run_id: str,
    path: str,
    source_path: str,
    ep: RawEndpoint,
    *,
    run_asset_id: str | None = None,
    asset_url: str | None = None,
    finding_type: FindingType = FindingType.ENDPOINT_UNRESOLVED,
) -> int:
    """Write a non-confirmed finding under the given ``finding_type`` from a best-effort
    ``_collapse_url`` skeleton: a Tier-4 ``ENDPOINT_UNRESOLVED`` sink we detected but couldn't
    resolve, a Tier-5 ``ENDPOINT_GENERIC`` suspected generic-client call, or a Phase-2
    ``PAGE_ROUTE`` client-navigation target (blank method, so its value is the bare URL, plus
    an ``attributes['confidence']`` of ``high``/``low``).

    Every one of these distinct types is excluded, with no per-consumer filter, from the
    OpenAPI export, shadow classification, and the endpoint headline count (all keyed on
    ``type == 'endpoint'``). The value is ``method + skeleton`` (a blank method collapses to
    just the skeleton) and is NOT run through ``normalize_endpoint`` — there is no real URL to
    host-strip or ``{id}``-template — and no params are recorded (the URL, hence its query, is
    unknown). The full call rides on the occurrence ``evidence`` so the analyst can resolve it
    by hand."""
    value = f"{ep.method} {ep.url}".strip()
    attributes: dict[str, Any] = {"kind": ep.kind, "method": ep.method}
    if ep.wrapper:  # provenance: the taught wrapper this sink came through (display-only)
        attributes["wrapper"] = ep.wrapper
    if finding_type == FindingType.PAGE_ROUTE:  # route-lane display confidence: "high" | "low"
        attributes["confidence"] = ep.confidence
    return _write(
        session,
        tenant_id,
        run_id,
        finding_type,
        value,
        path,
        occurrence=store.Occurrence(
            # DEBT D24: these lanes keep the raw URL literal in `value`, so when it is an
            # absolute http(s) URL its host is recoverable — lift it onto the occurrence so
            # the Findings host facet/chip can pivot on it. `attributed_host` is STRICTER
            # than the confirmed path (validates the host) because this lane also carries
            # unresolved/mangled junk; a relative path or template literal stays host-less.
            host=egress.attributed_host(ep.url),
            raw_url=ep.url,
            source_path=source_path,
            line=ep.line,
            col=ep.col,
            offset_start=ep.start_byte,
            offset_end=ep.end_byte,
            evidence=ep.snippet,
            engine="vespasian",
            run_asset_id=run_asset_id,
            asset_url=asset_url,
        ),
        attributes=attributes,
    )


def _record_secret(
    session: Session,
    tenant_id: str,
    run_id: str,
    path: str,
    source: str,
    secret: RawSecret,
    cursors: dict[tuple[str, str], int],
    *,
    finding_type: FindingType = FindingType.SECRET,
    source_path: str = _SOURCE_NAME,
    run_asset_id: str | None = None,
    asset_url: str | None = None,
) -> int:
    # value = provider:sha256(token) — the raw token is never hashed in cleartext.
    # D33-B: ``finding_type`` is SECRET (precision lane) or SECRET_SUSPECTED (opt-in
    # low-confidence recall); both use the identical reveal/redaction machinery below —
    # only the type (hence the finding identity + read-model bucket) differs.
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
    offset_start: int | None
    offset_end: int | None
    if located is not None:
        offset_start, offset_end = located
        cursors[key] = offset_end  # next identical sighting searches past this one
    else:
        offset_start = offset_end = None
    return _write(
        session,
        tenant_id,
        run_id,
        finding_type,
        value,
        path,
        occurrence=store.Occurrence(
            # D32-B1: the occurrence's source_path is the discriminator reveal uses to
            # pick its byte space — _SOURCE_NAME ("input.js") == the raw bundle (slice the
            # blob), a recovered path == the source map's original (re-derive it). The raw
            # recovered `f.path`, matching the endpoint occurrences' source_path so the
            # Sources tab joins them and reveal's `recover_file_text(map, path)` finds it.
            source_path=source_path,
            line=secret.line,
            col=secret.column_start,
            offset_start=offset_start,
            offset_end=offset_end,
            engine="kingfisher",
            confidence=secret.confidence,
            verified=True if secret.validation_status == "Active" else None,
            run_asset_id=run_asset_id,
            asset_url=asset_url,
        ),
        attributes={"rule": secret.rule_id, "name": secret.rule_name},
    )


def _record_internal_ip(
    session: Session,
    tenant_id: str,
    run_id: str,
    path: str,
    source_path: str,
    sighting: internal_ip.InternalIpSighting,
    *,
    run_asset_id: str | None = None,
    asset_url: str | None = None,
) -> int:
    # value = the RAW cleartext dotted-quad. Unlike ``_record_secret`` this NEVER calls
    # ``normalize.normalize_secret_value`` — an internal IP is info-disclosure, not a
    # secret: it is plainly visible, so it is stored in CLEARTEXT as ``finding.value``
    # (``finding_hash`` folds in the cleartext value only for identity/dedup — REQ-D3 —
    # not to redact it), never server-redacted, and never reveal-gated (``internal_ip`` is
    # deliberately absent from the ``_finding_view`` is_secret tuple and the ``reveal.py``
    # type filter). Offsets are char offsets in the SAME unit text the detector scanned
    # (the raw bundle or a recovered original); ``source_path`` threads through so the
    # Sources tab joins this sighting to the endpoint/secret occurrences of that unit.
    return _write(
        session,
        tenant_id,
        run_id,
        FindingType.INTERNAL_IP,
        sighting.value,
        path,
        occurrence=store.Occurrence(
            source_path=source_path,
            line=sighting.line,
            col=None,
            offset_start=sighting.offset_start,
            offset_end=sighting.offset_end,
            engine="internal-ip",
            confidence=None,
            run_asset_id=run_asset_id,
            asset_url=asset_url,
        ),
        attributes={"category": sighting.category},
    )


def _record_recovered_secrets(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    units: AnalysisUnits,
    run_asset_id: str | None,
    asset_url: str | None,
    confidence: str | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> tuple[int, int, int]:
    """Secret-scan the source-map-recovered originals (D32-B1); return
    (rows_written, secret_sightings, suspected_sightings).

    ONE Kingfisher pass over the on-disk BEAUTIFIED tree (`scan_dir`, D37-L2 slice 3) — a single
    subprocess, not one per file (a real bundle's map recovers dozens–hundreds), and never the whole
    tree in RAM. Each sighting is attributed to its recovered `source_path` and located by re-reading
    THAT one beautified file — the SAME `beautify_if_minified` text `sources.recover_file_text`
    reproduces at reveal time — so an audited reveal round-trips (and any drift fail-closes 409, never
    leaks). A genuine engine failure still RAISES (via `scan_dir`) to fail/retry the stage; an absent
    binary yields no sightings (the bundle scan already reports the honest status).

    D33-B: ``confidence`` matches the bundle scan's; each sighting is partitioned into SECRET vs
    SECRET_SUSPECTED by its own confidence, and the two counts are returned separately so the caller
    keeps the precision `secrets` count clean."""
    if units.is_bundle or units.tree_root is None:
        return 0, 0, 0
    if heartbeat is not None:
        heartbeat()  # before the (one, whole-tree) scan — S4
    # M4: scale the buffered-output cap with the tree so a `--no-dedup` JSONL over a big recovered
    # tree doesn't overflow engine_max_output_bytes and false-EngineError into the retry loop. The
    # tree is itself bounded (the write budget), so this ceiling is bounded too.
    scan_output_cap = max(get_settings().engine_max_output_bytes, units.tree_bytes)
    by_path, _status = kingfisher.scan_dir(
        units.tree_root, max_output_bytes=scan_output_cap, confidence=confidence
    )
    if heartbeat is not None:
        heartbeat()  # after the scan (a big tree's scan must not outlast the stall window) — S4
    written = 0
    sighted = 0
    suspected = 0
    for source_path, secrets in by_path.items():
        unit_text = units.read_text(source_path)  # ONE beautified file, for content-located offsets
        finding_path = normalize.normalize_source_path(source_path)
        # Fresh cursor per unit: offsets are located within THIS unit's own byte space,
        # so N identical sightings in one file still map to N distinct offsets (REQ-C2).
        cursors: dict[tuple[str, str], int] = {}
        for secret in secrets:
            finding_type = _secret_finding_type(secret)
            written += _record_secret(
                session,
                tenant_id,
                run_id,
                finding_path,
                unit_text,
                secret,
                cursors,
                finding_type=finding_type,
                source_path=source_path,
                run_asset_id=run_asset_id,
                asset_url=asset_url,
            )
            if finding_type is FindingType.SECRET_SUSPECTED:
                suspected += 1
            else:
                sighted += 1
    return written, sighted, suspected


def _record_recovered_internal_ips(
    session: Session,
    units: AnalysisUnits,
    *,
    tenant_id: str,
    run_id: str,
    run_asset_id: str | None,
    asset_url: str | None,
    heartbeat: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Record cleartext internal-IP literals in each source-map-recovered original (D33 gap 2),
    reading ONE beautified file at a time from the on-disk tree (D37-L2 slice 3). Returns
    (rows_written, sightings). A no-map bundle has no recovered units (its IPs scan the raw blob in
    `_analyze_blob`). Beats before each file (S4) so a big tree's IP scan stays lease-safe."""
    if units.is_bundle:
        return 0, 0
    written = 0
    sighted = 0
    for source_path in units.names:
        if heartbeat is not None:
            heartbeat()  # lease renew + REQ-A4, before each recovered file's IP scan (S4)
        unit_text = units.read_text(source_path)
        finding_path = normalize.normalize_source_path(source_path)
        for sighting in internal_ip.find_internal_ips(unit_text):
            written += _record_internal_ip(
                session,
                tenant_id,
                run_id,
                finding_path,
                source_path,
                sighting,
                run_asset_id=run_asset_id,
                asset_url=asset_url,
            )
            sighted += 1
    return written, sighted


def _secret_finding_type(secret: RawSecret) -> FindingType:
    """Partition a Kingfisher sighting (D33-B): a ``low``-confidence hit is the opt-in
    SECRET_SUSPECTED recall lane; medium/high (or an unreported confidence) is a
    precision-lane SECRET. Only reachable as SECRET_SUSPECTED under a `--confidence low`
    sweep — the default medium scan emits nothing at ``low``."""
    return FindingType.SECRET_SUSPECTED if secret.confidence == "low" else FindingType.SECRET


def _record_graphql_operations(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    source: str,
    asset_url: str | None,
) -> None:
    """Persist this blob's GraphQL operations as a run-level export artifact (enrichment C).

    Mirrors the discover assets-manifest (``crawl.py``): store a content-addressed
    ``graphql`` blob and index it with an ``analyze.graphql`` event. Export-only — a GraphQL
    operation is NOT an HTTP endpoint (it rides one POST to a ``/graphql`` route), so it is
    never written as a finding or an OpenAPI path and never pollutes the HTTP-endpoints read
    model (locked decision 1). ``source_path`` is the asset URL when known, else the bundle
    fallback. Empty → no blob, no event.

    A crawl run analyzes once PER asset, so each asset emits its OWN ``analyze.graphql``
    event/blob; the export UNIONs them (``queries.graphql_operations``), so this deliberately
    does not aggregate run-wide. Not published to Redis: the sole consumer is the OpenAPI
    export, which reads the durable ``run_event`` log — there is no live GraphQL UI.
    """
    operations = graphql_ops.collect_operations(source)
    if not operations:
        return
    source_path = asset_url or _SOURCE_NAME
    entries: list[dict[str, Any]] = [
        {
            "op_type": op.op_type,
            "name": op.name,
            "fields": list(op.fields),
            "source_path": source_path,
        }
        for op in operations
    ]
    graphql_ref = storage.put_blob(
        tenant_id, run_id, "graphql", json.dumps(entries).encode("utf-8")
    )
    record_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type="analyze.graphql",
        payload={"count": len(entries), "graphql_ref": graphql_ref},
    )


def _write(
    session: Session,
    tenant_id: str,
    run_id: str,
    finding_type: str,
    value: str,
    path: str,
    *,
    occurrence: store.Occurrence,
    attributes: dict[str, Any],
) -> int:
    result = store.record_finding(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        finding_type=finding_type,
        value=value,
        path=path,
        occurrence=occurrence,
        attributes=attributes,
        first_stage="analyzing",
    )
    return int(result.finding_created) + int(result.occurrence_created)
