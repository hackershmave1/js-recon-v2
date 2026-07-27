"""Read model for a run's findings (REQ-D3, REQ-C2).

Kept apart from the write-side outbox (``store.py``) so reading findings stays a
plain tenant-scoped query. Isolation is the database's job: ``tenant_session``
sets the RLS GUC, so a run that belongs to another tenant is simply invisible —
``list_findings`` returns ``None`` (the HTTP layer maps that to 404), which is
deliberately distinct from a run that exists with zero findings (empty list).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from recon.db.base import tenant_session
from recon.db.models import Finding, FindingOccurrence, FindingTriage, Run, RunAsset, RunEvent
from recon.domain import FindingType


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
class FindingView:
    finding_hash: str
    type: str
    value: str
    path: str
    severity: str | None
    attributes: dict
    first_stage: str | None
    occurrences: list[OccurrenceView]
    triage: TriageView | None = None
    revealable: bool = False


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
            for a in session.scalars(
                select(RunAsset).where(RunAsset.run_id == str(run_id))
            ).all()
        }
        return FindingsView(
            run_id=str(run_id),
            findings=[
                _finding_view(
                    finding,
                    triage_by_hash.get(finding.finding_hash),
                    run.input_ref,
                    asset_refs,
                )
                for finding in findings
            ],
            coverage=_latest_coverage(session, run_id),
        )


def _latest_coverage(session, run_id: str) -> CoverageView | None:
    """The most recent ``analyze.coverage`` event for the run (a stage retry appends
    a fresh one; the highest id is authoritative). ``None`` until analyze has run."""
    payload = session.scalars(
        select(RunEvent.payload)
        .where(RunEvent.run_id == str(run_id), RunEvent.type == "analyze.coverage")
        .order_by(RunEvent.id.desc())
        .limit(1)
    ).first()
    if payload is None:
        return None
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


def _finding_view(
    finding: Finding,
    triage_row: FindingTriage | None = None,
    run_input_ref: str | None = None,
    asset_refs: dict[str, _AssetRef] | None = None,
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
    )


def _triage_view(row: FindingTriage | None) -> TriageView | None:
    if row is None:
        return None
    return TriageView(
        status=row.status, note=row.note, actor=row.actor,
        updated_at=row.updated_at.isoformat(),
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
