"""Persist findings via the REQ-A3 transactional-outbox pattern.

``record_finding`` upserts a finding (idempotent on ``run_id + finding_hash``) and
appends an occurrence (idempotent on ``finding_id + occurrence_hash``) inside the
caller's transaction, so a stage retry can never double-write (REQ-A3) while a
normalization merge still surfaces every distinct sighting (REQ-C2). It does no
commit of its own — the calling stage owns the transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from recon.db import models
from recon.findings import normalize


@dataclass(frozen=True)
class Occurrence:
    """One sighting's volatile detail. The identifying subset (raw_url, host,
    source_path, offsets) forms the ``occurrence_hash`` so retries dedupe."""

    host: str | None = None
    raw_url: str | None = None
    source_path: str | None = None
    line: int | None = None
    col: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    evidence: str | None = None
    engine: str | None = None
    confidence: str | None = None
    verified: bool | None = None

    def _identity(self) -> dict[str, object]:
        return {
            "raw_url": self.raw_url,
            "host": self.host,
            "source_path": self.source_path,
            "offset_start": self.offset_start,
            "offset_end": self.offset_end,
        }


@dataclass(frozen=True)
class RecordResult:
    finding_id: str
    finding_hash: str
    finding_created: bool
    occurrence_created: bool


def record_finding(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    finding_type: str,
    value: str,
    path: str,
    occurrence: Occurrence,
    severity: str | None = None,
    attributes: dict | None = None,
    first_stage: str | None = None,
) -> RecordResult:
    """Idempotently record one finding + one of its occurrences.

    ``value``/``path`` must already be normalized (see ``recon.findings.normalize``).
    Returns which rows were newly created so a caller can count real additions.
    """
    finding_hash = normalize.finding_hash(finding_type, value, path)

    insert_finding = (
        pg_insert(models.Finding)
        .values(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            finding_hash=finding_hash,
            type=str(finding_type),
            value=value,
            path=path,
            severity=severity,
            attributes=attributes or {},
            first_stage=first_stage,
        )
        .on_conflict_do_nothing(index_elements=["run_id", "finding_hash"])
        .returning(models.Finding.id)
    )
    finding_id = session.execute(insert_finding).scalar()
    finding_created = finding_id is not None
    if finding_id is None:  # already present (retry or a normalization merge)
        finding_id = session.execute(
            select(models.Finding.id).where(
                models.Finding.run_id == str(run_id),
                models.Finding.finding_hash == finding_hash,
            )
        ).scalar_one()

    occurrence_hash = normalize.occurrence_hash(**occurrence._identity())
    insert_occurrence = (
        pg_insert(models.FindingOccurrence)
        .values(
            tenant_id=str(tenant_id),
            finding_id=finding_id,
            occurrence_hash=occurrence_hash,
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
        .on_conflict_do_nothing(index_elements=["finding_id", "occurrence_hash"])
        .returning(models.FindingOccurrence.id)
    )
    occurrence_created = session.execute(insert_occurrence).scalar() is not None

    return RecordResult(
        finding_id=str(finding_id),
        finding_hash=finding_hash,
        finding_created=finding_created,
        occurrence_created=occurrence_created,
    )
