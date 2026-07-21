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
from recon.db.models import Finding, FindingOccurrence, Run, RunEvent


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


def list_findings(tenant_id: str, run_id: str) -> FindingsView | None:
    """Every finding for a run with its occurrences and the analyze coverage
    counters, or ``None`` if the run does not exist for this tenant. Ordered
    deterministically for stable output."""
    with tenant_session(tenant_id) as session:
        if session.get(Run, run_id) is None:
            return None
        findings = session.scalars(
            select(Finding)
            .where(Finding.run_id == str(run_id))
            # finding_hash is the stable tiebreaker: (type, value) is unique per run
            # only while the single-file MVP forces one path; once per-source paths
            # arrive (Sourcemapper) two findings can share (type, value).
            .order_by(Finding.type, Finding.value, Finding.finding_hash)
            .options(selectinload(Finding.occurrences))
        ).all()
        return FindingsView(
            run_id=str(run_id),
            findings=[_finding_view(finding) for finding in findings],
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


def _finding_view(finding: Finding) -> FindingView:
    return FindingView(
        finding_hash=finding.finding_hash,
        type=finding.type,
        value=finding.value,
        path=finding.path,
        severity=finding.severity,
        attributes=dict(finding.attributes or {}),
        first_stage=finding.first_stage,
        occurrences=[
            _occurrence_view(occurrence)
            for occurrence in sorted(
                finding.occurrences,
                key=lambda o: (o.source_path or "", o.offset_start or 0, o.occurrence_hash),
            )
        ],
    )


def _occurrence_view(occurrence: FindingOccurrence) -> OccurrenceView:
    return OccurrenceView(
        host=occurrence.host,
        raw_url=occurrence.raw_url,
        source_path=occurrence.source_path,
        line=occurrence.line,
        col=occurrence.col,
        offset_start=occurrence.offset_start,
        offset_end=occurrence.offset_end,
        evidence=occurrence.evidence,
        engine=occurrence.engine,
        confidence=occurrence.confidence,
        verified=occurrence.verified,
    )
