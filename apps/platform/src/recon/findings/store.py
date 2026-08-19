"""Persist findings via the REQ-A3 transactional-outbox pattern.

``record_finding`` upserts a finding (idempotent on ``run_id + finding_hash``) and
appends an occurrence (idempotent on ``finding_id + occurrence_hash``) inside the
caller's transaction, so a stage retry can never double-write (REQ-A3) while a
normalization merge still surfaces every distinct sighting (REQ-C2). It does no
commit of its own — the calling stage owns the transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
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
    # Slice Y asset dimension. asset_url is part of occurrence identity so the same
    # finding stays distinct per asset; run_asset_id is stored for reveal routing.
    run_asset_id: str | None = None
    asset_url: str | None = None

    def _identity(self) -> dict[str, object]:
        # line/col are part of identity so two sightings stay distinct even when
        # byte offsets are absent — e.g. Kingfisher reports line/col but no offset,
        # so an engine that yields no offset must not collapse two real secret
        # sightings into one occurrence (REQ-C2 honesty).
        return {
            "raw_url": self.raw_url,
            "host": self.host,
            "source_path": self.source_path,
            "offset_start": self.offset_start,
            "offset_end": self.offset_end,
            "line": self.line,
            "col": self.col,
            "asset_url": self.asset_url,
        }


@dataclass(frozen=True)
class RecordResult:
    finding_id: str
    finding_hash: str
    finding_created: bool
    occurrence_created: bool


def _merge_attributes(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Union the two findings' attributes when a normalization merge (v2: same
    type+value, different source file) collapses them onto one row.

    Load-bearing for REQ-C2 honesty: the finding row is inserted ``ON CONFLICT DO
    NOTHING``, so without this the second sighting's ``attributes`` are discarded
    and an observed ``auth`` header (attack surface) or ``risk_tag`` would vanish.
    Security-relevant lists are unioned; ``kind`` degrades to ``None`` when two
    sightings disagree (so the reconstructor never asserts a wrong Content-Type
    for an operation seen via, say, both ``fetch`` JSON and jQuery form-encoding);
    ``wrapper`` keeps the first non-null. Pure + order-independent so an
    at-least-once retry re-merging identical inputs is a no-op."""
    merged = dict(existing)
    # auth: list of {name, scheme} — append the not-yet-present entries (the
    # reconstructor re-dedupes by name at read time, so exact-dict union suffices).
    incoming_auth = incoming.get("auth") or []
    if incoming_auth:
        combined = list(existing.get("auth") or [])
        for header in incoming_auth:
            if header not in combined:
                combined.append(header)
        merged["auth"] = combined
    # risk_tags: set-union of string tags.
    incoming_tags = incoming.get("risk_tags") or []
    if incoming_tags:
        merged["risk_tags"] = sorted(set(existing.get("risk_tags") or []) | set(incoming_tags))
    # kind: agree -> keep; disagree -> None (ambiguous, assert nothing).
    if "kind" in incoming:
        if "kind" not in existing:
            merged["kind"] = incoming["kind"]
        elif existing.get("kind") != incoming.get("kind"):
            merged["kind"] = None
    # wrapper (and any other scalar): first non-null wins; fill only if absent.
    for key, val in incoming.items():
        if key in ("auth", "risk_tags", "kind"):
            continue
        if merged.get(key) is None and val is not None:
            merged[key] = val
    return merged


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
    attributes: dict[str, Any] | None = None,
    first_stage: str | None = None,
) -> RecordResult:
    """Idempotently record one finding + one of its occurrences.

    ``value``/``path`` must already be normalized (see ``recon.findings.normalize``).
    Returns which rows were newly created so a caller can count real additions.
    """
    finding_hash = normalize.finding_hash(finding_type, value)

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
        existing_id, existing_attrs = session.execute(
            select(models.Finding.id, models.Finding.attributes).where(
                models.Finding.run_id == str(run_id),
                models.Finding.finding_hash == finding_hash,
            )
        ).one()
        finding_id = existing_id
        # A v2 merge collapses two findings that used to differ only by source path
        # onto this one row. The insert above did nothing, so union the incoming
        # attributes into the stored ones — otherwise the first writer's auth headers
        # win and a later sighting's observed header (attack surface) is silently lost
        # (REQ-C2). Write only on an actual change so an A3 retry stays a no-op.
        merged_attrs = _merge_attributes(existing_attrs or {}, attributes or {})
        if merged_attrs != (existing_attrs or {}):
            session.execute(
                update(models.Finding)
                .where(models.Finding.id == existing_id)
                .values(attributes=merged_attrs)
            )

    occurrence_hash = normalize.occurrence_hash(**occurrence._identity())
    insert_occurrence = (
        pg_insert(models.FindingOccurrence)
        .values(
            tenant_id=str(tenant_id),
            finding_id=finding_id,
            run_asset_id=occurrence.run_asset_id,
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
