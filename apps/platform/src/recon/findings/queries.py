"""Read model for a run's findings (REQ-D3, REQ-C2).

Kept apart from the write-side outbox (``store.py``) so reading findings stays a
plain tenant-scoped query. Isolation is the database's job: ``tenant_session``
sets the RLS GUC, so a run that belongs to another tenant is simply invisible —
``list_findings`` returns ``None`` (the HTTP layer maps that to 404), which is
deliberately distinct from a run that exists with zero findings (empty list).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, selectinload

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import (
    EngagementSession,
    Finding,
    FindingOccurrence,
    FindingSpecStatus,
    FindingTriage,
    Run,
    RunAsset,
    RunEvent,
    SessionBaseUrl,
    SessionSpec,
    SessionWrapper,
)
from recon.domain import FindingType
from recon.findings.base_url import BaseUrlRule
from recon.findings.wrappers import WrapperRule
from recon.spec.classify import Classification, SpecSummary, summarize


@dataclass(frozen=True)
class OccurrenceView:
    host: str | None
    raw_url: str | None
    source_path: str | None
    line: int | None
    col: int | None
    offset_start: int | None
    offset_end: int | None
    evidence: str | None
    engine: str | None
    confidence: str | None
    verified: bool | None
    # Slice Y: which discovered asset this sighting came from, resolved from
    # run_asset_id -> run_asset.url. None for legacy (pre-crawl) occurrences.
    asset_url: str | None = None


@dataclass(frozen=True)
class TriageView:
    status: str
    note: str | None
    actor: str | None
    updated_at: str


@dataclass(frozen=True)
class SpecStatusView:
    """The shadow-API verdict (design §6.4) for one endpoint finding, read
    straight off its stored ``FindingSpecStatus`` row. Absent entirely
    (``FindingView.spec_status is None``) means "never classified" -- either
    no spec is attached to the session, or this finding is not an endpoint --
    which the API renders as ``unclassified``, distinct from any real verdict."""

    status: str
    reason: str | None
    matched_operation: str | None


@dataclass(frozen=True)
class SightingSummary:
    """Slice 4: cross-run "sightings" of one finding — how many OTHER runs in the
    same engagement carry the same ``finding_hash``, split by origin. ``capture`` =
    a browser-extension capture session (``external_id`` set) whose run is not an
    upload; ``platform`` = a crawl or an uploaded bundle. A ``FindingView.sightings``
    of ``None`` means the run's session has no engagement (ungrouped), so no
    cross-run collapse was attempted -- distinct from ``capture == platform == 0``
    (grouped, but this finding is unique to the run)."""

    capture: int
    platform: int


@dataclass(frozen=True)
class FindingView:
    finding_hash: str
    type: str
    value: str
    path: str
    severity: str | None
    attributes: dict[str, Any]
    first_stage: str | None
    occurrences: list[OccurrenceView]
    triage: TriageView | None = None
    revealable: bool = False
    spec_status: SpecStatusView | None = None
    # Slice 4: cross-run collapse. `None` == ungrouped (the run's session has no
    # engagement); a summary (possibly all-zero) == grouped and computed.
    sightings: SightingSummary | None = None


@dataclass(frozen=True)
class FileCoverageView:
    path: str
    attributed: int
    unattributed: int


@dataclass(frozen=True)
class CoverageView:
    """The analyze stage's honesty counters (REQ-C2), surfaced next to the findings
    they qualify. ``unattributed`` cannot be recomputed from stored findings (an
    un-attributed call yields no finding row), so it is read from the durable event
    log — the record of what analyze actually reported."""

    attributed: int
    unattributed: int
    secrets: int
    secrets_engine: str
    sources_recovered: int
    source_map: str
    files: list[FileCoverageView]


@dataclass(frozen=True)
class FindingsView:
    run_id: str
    findings: list[FindingView]
    coverage: CoverageView | None
    # Design §6.4: run-scoped shadow-API bucket counts. `None` until a spec is
    # attached to the run's session at all (distinct from "attached but every
    # bucket is 0") -- mirrors `coverage`'s "null until analyze has run" shape.
    spec_summary: SpecSummary | None = None


@dataclass(frozen=True)
class _AssetRef:
    """A run_asset's blob pointer and URL, keyed by run_asset_id — one lookup
    serves both `revealable`'s blob resolution and the occurrence's asset_url."""

    input_ref: str | None
    url: str


def list_findings(tenant_id: str, run_id: str) -> FindingsView | None:
    """Every finding for a run with its occurrences and the analyze coverage
    counters, or ``None`` if the run does not exist for this tenant. Ordered
    deterministically for stable output."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        triage_by_hash = {
            row.finding_hash: row
            for row in session.scalars(
                select(FindingTriage).where(FindingTriage.session_id == str(run.session_id))
            ).all()
        }
        # Session-scoped, exactly like triage_by_hash above: a spec-status verdict
        # outlives any one run (REQ-D5 continuous rescan), keyed off finding_hash,
        # so it's read once for the whole session and matched to THIS run's own
        # findings below (the same finding recurring across runs shares a verdict).
        spec_status_by_hash = {
            row.finding_hash: row
            for row in session.scalars(
                select(FindingSpecStatus).where(FindingSpecStatus.session_id == str(run.session_id))
            ).all()
        }
        # Slice 4: cross-run "sightings" — other runs in THIS engagement sharing each
        # finding_hash. Engagement-scoped (finding_hash carries no app identity, so a
        # tenant-wide match would false-link unrelated targets); skipped entirely when
        # the session has no engagement (ungrouped) so we never emit `= NULL`, and the
        # `None` propagates to every FindingView as the "ungrouped" tri-state.
        engagement_id = session.scalar(
            select(EngagementSession.engagement_id).where(
                EngagementSession.id == str(run.session_id)
            )
        )
        sightings_by_hash = (
            _cross_run_sightings(session, str(engagement_id), str(run_id))
            if engagement_id is not None
            else None
        )
        findings = session.scalars(
            select(Finding)
            .where(Finding.run_id == str(run_id))
            # finding_hash is the stable tiebreaker: (type, value) is unique per run
            # only while the single-file MVP forces one path; once per-source paths
            # arrive (Sourcemapper) two findings can share (type, value).
            .order_by(Finding.type, Finding.value, Finding.finding_hash)
            .options(selectinload(Finding.occurrences))
        ).all()
        # Slice Y: a crawl run's bytes live per-asset (run.input_ref is NULL for
        # those runs), so `revealable` must be computed from each occurrence's own
        # asset blob, not the run-level ref — and the FE needs the asset's URL for
        # attribution. One query for every asset the run owns serves both.
        asset_refs = {
            str(a.id): _AssetRef(input_ref=a.input_ref, url=a.url)
            for a in session.scalars(select(RunAsset).where(RunAsset.run_id == str(run_id))).all()
        }
        # Design §6.4: a real (possibly all-zero) summary once a spec is attached
        # to the session at all; `None` distinguishes "never attached" from
        # "attached, nothing classified yet" -- an existence check, not a row read,
        # since spec_status_by_hash above already carries every verdict this
        # session has.
        has_session_spec = (
            session.scalar(
                select(SessionSpec.id).where(SessionSpec.session_id == str(run.session_id))
            )
            is not None
        )
        return FindingsView(
            run_id=str(run_id),
            findings=[
                _finding_view(
                    finding,
                    triage_by_hash.get(finding.finding_hash),
                    run.input_ref,
                    asset_refs,
                    spec_status_by_hash.get(finding.finding_hash),
                    sightings=(
                        None
                        if sightings_by_hash is None
                        else sightings_by_hash.get(
                            finding.finding_hash, SightingSummary(capture=0, platform=0)
                        )
                    ),
                )
                for finding in findings
            ],
            coverage=_latest_coverage(session, run_id, is_multi_asset=bool(asset_refs)),
            spec_summary=(
                _run_spec_summary(findings, spec_status_by_hash) if has_session_spec else None
            ),
        )


def _cross_run_sightings(
    session: Session, engagement_id: str, run_id: str
) -> dict[str, SightingSummary]:
    """Per ``finding_hash``, the count of OTHER runs (excluding ``run_id``) in this
    engagement that carry it, bucketed by origin. One grouped query; RLS confines it
    to the tenant and the engagement filter keeps an unrelated target that collides
    on ``finding_hash`` from cross-linking (the hash carries no app identity)."""
    # origin == `capture` only for an extension-capture accumulator: a capture SESSION
    # (external_id set) whose RUN is not an upload. The `run.input_ref IS NULL` guard
    # excludes an /runs/upload run dropped into a capture session (external_id set,
    # input_ref set) -- that is a `platform` upload, not an extension capture. A crawl
    # run leaves input_ref NULL but its session's external_id is NULL, so it stays
    # `platform` via the else branch.
    origin = case(
        (
            and_(EngagementSession.external_id.is_not(None), Run.input_ref.is_(None)),
            "capture",
        ),
        else_="platform",
    ).label("origin")
    rows = session.execute(
        select(Finding.finding_hash, origin, func.count().label("n"))
        .join(Run, Run.id == Finding.run_id)
        .join(EngagementSession, EngagementSession.id == Run.session_id)
        .where(
            EngagementSession.engagement_id == engagement_id,
            Finding.run_id != run_id,
        )
        .group_by(Finding.finding_hash, origin)
    ).all()
    buckets: dict[str, dict[str, int]] = {}
    for finding_hash, origin_label, count in rows:
        buckets.setdefault(finding_hash, {"capture": 0, "platform": 0})[origin_label] = count
    return {
        finding_hash: SightingSummary(capture=b["capture"], platform=b["platform"])
        for finding_hash, b in buckets.items()
    }


def base_url_rules_in_session(session: Session, session_id: str) -> list[BaseUrlRule]:
    """Every manual base-URL rule for a session, as pure BaseUrlRule values.
    Takes an OPEN tenant session so a caller (e.g. _classify_session) can load
    rules inside its own transaction."""
    # Order most-recently-updated first so the resolver's "first matching selection
    # wins" (base_url._match) realizes the spec §6 tie-break — most-recent updated_at
    # — for OVERLAPPING selection rules. Each rule is its own POST/transaction, so
    # updated_at (transaction time) is distinct in practice and true ties don't arise;
    # the id secondary only makes the order total/deterministic (it is a random UUID,
    # not a recency signal). Prefix rules are unique per prefix and resolved
    # longest-wins, so their order here is moot.
    rows = session.scalars(
        select(SessionBaseUrl)
        .where(SessionBaseUrl.session_id == session_id)
        .order_by(SessionBaseUrl.updated_at.desc(), SessionBaseUrl.id.desc())
    ).all()
    return [
        BaseUrlRule(
            kind=row.kind,
            base_url=row.base_url,
            path_prefix=row.path_prefix,
            finding_hashes=tuple(row.finding_hashes or ()),
        )
        for row in rows
    ]


def wrapper_rules_in_session(session: Session, session_id: str) -> list[WrapperRule]:
    """Every taught wrapper callee for a session, as pure WrapperRule values. Takes
    an OPEN tenant session so a caller can load rules inside its own transaction
    (mirrors ``base_url_rules_in_session``)."""
    rows = session.scalars(
        select(SessionWrapper)
        .where(SessionWrapper.session_id == session_id)
        .order_by(SessionWrapper.created_at)
    ).all()
    return [WrapperRule(callee=row.callee) for row in rows]


def list_base_url_rules(tenant_id: str, run_id: str) -> list[BaseUrlRule]:
    """The base-URL rules for a run's session, opening a tenant transaction.
    Empty list if the run is invisible to the tenant (RLS) or does not exist."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return []
        return base_url_rules_in_session(session, str(run.session_id))


def graphql_operations(tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    """Every GraphQL operation recorded for the run, unioned across its assets (enrichment C).

    A crawl run analyzes once PER asset, each emitting its OWN ``analyze.graphql`` event that
    indexes its OWN content-addressed ``graphql`` blob (ops are stored in the blob, not inline
    in the event — REQ-D2 keeps artifacts out of Postgres). The run-wide set is therefore the
    UNION of every event's blob, deduped: reading only the latest event would drop all but the
    last asset's ops (design-gate M4). Deterministic order; ``[]`` when the run has no GraphQL
    (or is invisible to the tenant — RLS simply yields no events).
    """
    with tenant_session(tenant_id) as session:
        payloads = session.scalars(
            select(RunEvent.payload)
            .where(RunEvent.run_id == str(run_id), RunEvent.type == "analyze.graphql")
            .order_by(RunEvent.id)
        ).all()
    seen: set[tuple[str, str | None, tuple[str, ...], str]] = set()
    operations: list[dict[str, Any]] = []
    for payload in payloads:
        ref = payload.get("graphql_ref")
        if not ref:
            continue
        for entry in json.loads(storage.get_blob(ref).decode("utf-8")):
            identity = (
                entry["op_type"],
                entry.get("name"),
                tuple(entry.get("fields", [])),
                entry["source_path"],
            )
            if identity not in seen:
                seen.add(identity)
                operations.append(entry)
    return sorted(
        operations,
        key=lambda op: (
            op["op_type"],
            op.get("name") or "",
            tuple(op["fields"]),
            op["source_path"],
        ),
    )


def _latest_coverage(session: Session, run_id: str, *, is_multi_asset: bool) -> CoverageView | None:
    """The run's analyze coverage counters (REQ-C2).

    A multi-asset (crawl) run's assets each emit their OWN ``analyze.coverage``
    event, exactly once — a redelivery skips an already analyze-terminal asset
    rather than re-analyzing it, so no asset's event is ever re-emitted. The true
    run-wide total is therefore the SUM of every one of the run's events, not just
    the highest-id one: taking only the latest silently dropped every earlier
    asset's counts (e.g. reporting ``secrets: 0`` even though an earlier asset's
    own event had recorded some).

    A legacy single-asset run instead has exactly one analyze stage, which a
    retry re-runs IN PLACE and which re-emits its one event again — there,
    summing would double-count a retry, so the highest-id event alone (latest
    wins, the original behavior) is what's correct.

    ``None`` until analyze has emitted at least one event for the run.
    """
    payloads = session.scalars(
        select(RunEvent.payload)
        .where(RunEvent.run_id == str(run_id), RunEvent.type == "analyze.coverage")
        .order_by(RunEvent.id.desc())
    ).all()
    if not payloads:
        return None
    payload = _merge_coverage_payloads(payloads) if is_multi_asset else payloads[0]
    return _coverage_view_from_payload(payload)


def _coverage_view_from_payload(payload: dict[str, Any]) -> CoverageView:
    return CoverageView(
        attributed=int(payload.get("attributed", 0)),
        unattributed=int(payload.get("unattributed", 0)),
        secrets=int(payload.get("secrets", 0)),
        secrets_engine=str(payload.get("secrets_engine", "ok")),
        sources_recovered=int(payload.get("sources_recovered", 0)),
        source_map=str(payload.get("source_map", "none")),
        files=[
            FileCoverageView(
                path=str(entry.get("path", "")),
                attributed=int(entry.get("attributed", 0)),
                unattributed=int(entry.get("unattributed", 0)),
            )
            for entry in payload.get("files", [])
        ],
    )


def _merge_coverage_payloads(payloads: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Sum every asset's own coverage payload into one run-wide payload (Slice Y).

    Mirrors ``analyze._merge_coverage``'s semantics exactly: counts are additive;
    ``secrets_engine`` is the LESS-healthy of all the events ("unavailable" wins
    over "ok") so one asset's absent scanner is never masked by another asset's
    clean scan (REQ-C2); ``files`` (per-source-path detail) is concatenated across
    assets rather than merged path-by-path — it is per-file-per-asset detail, so
    two assets sharing the same fallback path still get two distinct entries;
    ``source_map`` is not meaningful to merge (every asset this slice analyzes
    with ``source_map_ref=None``), so any one value stands in — the first
    (highest-id) event's.
    """
    files: list[dict[str, Any]] = []
    for payload in payloads:
        files.extend(payload.get("files", []))
    return {
        "attributed": sum(int(p.get("attributed", 0)) for p in payloads),
        "unattributed": sum(int(p.get("unattributed", 0)) for p in payloads),
        "secrets": sum(int(p.get("secrets", 0)) for p in payloads),
        "secrets_engine": (
            "unavailable"
            if any(p.get("secrets_engine") == "unavailable" for p in payloads)
            else "ok"
        ),
        "sources_recovered": sum(int(p.get("sources_recovered", 0)) for p in payloads),
        "source_map": payloads[0].get("source_map", "none"),
        "files": files,
    }


def _run_spec_summary(
    findings: Sequence[Finding], spec_status_by_hash: dict[str, FindingSpecStatus]
) -> SpecSummary:
    """The design §5.4/§6.4 run-scoped summary — bucket-count the verdicts
    already stored for THIS run's own endpoint findings.

    Mirrors ``recon.spec.service._run_scoped_summary``'s run-narrowing (storage
    is session-scoped, but the summary a caller sees is run-scoped), but reuses
    the ``findings``/``spec_status_by_hash`` this query already loaded rather
    than a second round-trip. A finding with no row in ``spec_status_by_hash``
    (not yet classified, e.g. it postdates the last attach/reclassify) is
    simply excluded from the count — the same "no verdict yet" gap
    ``spec_status`` itself leaves as ``None`` on that finding.
    """
    classifications = (
        Classification(row.status, row.reason or "", row.matched_operation)
        for finding in findings
        if finding.type == FindingType.ENDPOINT.value
        for row in [spec_status_by_hash.get(finding.finding_hash)]
        if row is not None
    )
    return summarize(classifications)


def _finding_view(
    finding: Finding,
    triage_row: FindingTriage | None = None,
    run_input_ref: str | None = None,
    asset_refs: dict[str, _AssetRef] | None = None,
    spec_status_row: FindingSpecStatus | None = None,
    sightings: SightingSummary | None = None,
) -> FindingView:
    # REQ-S2: a secret's raw evidence is never served; the value comes only from the
    # audited reveal endpoint. Endpoint/param evidence (a code snippet) is kept.
    is_secret = finding.type == FindingType.SECRET.value
    asset_refs = asset_refs or {}

    def _asset_url_for(occurrence: FindingOccurrence) -> str | None:
        # Slice Y: resolve the occurrence's own asset URL for FE attribution.
        # None for legacy occurrences (run_asset_id NULL, pre-crawl runs).
        if occurrence.run_asset_id is None:
            return None
        ref = asset_refs.get(str(occurrence.run_asset_id))
        return ref.url if ref else None

    occurrences = [
        _occurrence_view(
            occurrence, redact_evidence=is_secret, asset_url=_asset_url_for(occurrence)
        )
        for occurrence in sorted(
            finding.occurrences,
            key=lambda o: (o.source_path or "", o.offset_start or 0, o.occurrence_hash),
        )
    ]

    def _blob_for(occurrence: FindingOccurrence) -> str | None:
        # Slice Y: an asset-tagged occurrence reveals from its own asset blob;
        # a legacy occurrence (run_asset_id NULL) falls back to run.input_ref.
        if occurrence.run_asset_id is not None:
            ref = asset_refs.get(str(occurrence.run_asset_id))
            return ref.input_ref if ref else None
        return run_input_ref

    revealable = bool(
        is_secret
        and any(
            o.offset_start is not None and o.offset_end is not None and _blob_for(o)
            for o in finding.occurrences
        )
    )
    return FindingView(
        finding_hash=finding.finding_hash,
        type=finding.type,
        value=finding.value,
        path=finding.path,
        severity=finding.severity,
        attributes=dict(finding.attributes or {}),
        first_stage=finding.first_stage,
        occurrences=occurrences,
        triage=_triage_view(triage_row),
        revealable=revealable,
        spec_status=_spec_status_view(spec_status_row),
        sightings=sightings,
    )


def _triage_view(row: FindingTriage | None) -> TriageView | None:
    if row is None:
        return None
    return TriageView(
        status=row.status,
        note=row.note,
        actor=row.actor,
        updated_at=row.updated_at.isoformat(),
    )


def _spec_status_view(row: FindingSpecStatus | None) -> SpecStatusView | None:
    if row is None:
        return None
    return SpecStatusView(
        status=row.status, reason=row.reason, matched_operation=row.matched_operation
    )


def _occurrence_view(
    occurrence: FindingOccurrence,
    redact_evidence: bool = False,
    asset_url: str | None = None,
) -> OccurrenceView:
    return OccurrenceView(
        host=occurrence.host,
        raw_url=occurrence.raw_url,
        source_path=occurrence.source_path,
        line=occurrence.line,
        col=occurrence.col,
        offset_start=occurrence.offset_start,
        offset_end=occurrence.offset_end,
        evidence=None if redact_evidence else occurrence.evidence,
        engine=occurrence.engine,
        confidence=occurrence.confidence,
        verified=occurrence.verified,
        asset_url=asset_url,
    )
