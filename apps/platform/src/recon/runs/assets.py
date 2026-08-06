"""Per-asset run state (Slice Y). One row per discovered asset; created by discover,
mutated by fetch/analyze, aggregated by the coordinator for REQ-D5 completeness.

Status setters take the caller's ``session`` so a stage can commit an asset's status
together with its side effects in that asset's own transaction (best-effort survives an
infra-error retry). ``list_for_run`` owns its read transaction and returns detached rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import AssetStatus

_ERR_CAP = 500  # keep a single asset's error message bounded


@dataclass(frozen=True)
class AssetRow:
    id: str
    url: str
    input_ref: str | None
    fetch_status: str
    analyze_status: str
    # Optional per-asset source map blob key (kind="source_map"); None for a
    # crawled asset or one the extension captured without a map. Default keeps
    # existing constructors (tests, older callers) working unchanged.
    source_map_ref: str | None = None
    # Why a fetch/analyze FAILED (bounded string) so the UI can surface a blocked
    # crawl's dominant reason (e.g. "target returned HTTP 403"); None otherwise.
    fetch_error: str | None = None
    analyze_error: str | None = None


def seed_pending(session: Session, *, tenant_id: str, run_id: str, urls: list[str]) -> None:
    if not urls:
        return
    session.execute(
        pg_insert(models.RunAsset)
        .values([
            {"tenant_id": str(tenant_id), "run_id": str(run_id), "url": u} for u in urls
        ])
        .on_conflict_do_nothing(index_elements=["run_id", "url"])
    )


def list_for_run(tenant_id: str, run_id: str) -> list[AssetRow]:
    with tenant_session(tenant_id) as session:
        rows = session.scalars(
            select(models.RunAsset)
            .where(models.RunAsset.run_id == str(run_id))
            .order_by(models.RunAsset.url)
        ).all()
        return [
            AssetRow(
                id=str(r.id), url=r.url, input_ref=r.input_ref,
                fetch_status=r.fetch_status, analyze_status=r.analyze_status,
                source_map_ref=r.source_map_ref,
                fetch_error=r.fetch_error, analyze_error=r.analyze_error,
            )
            for r in rows
        ]


def _set(session: Session, asset_id: str, values: dict) -> None:
    session.execute(
        update(models.RunAsset).where(models.RunAsset.id == asset_id).values(**values)
    )


def set_fetch_ok(session: Session, asset_id: str, input_ref: str) -> None:
    _set(
        session, asset_id,
        {"input_ref": input_ref, "fetch_status": AssetStatus.OK.value, "fetch_error": None}
    )


def set_source_map_ref(session: Session, asset_id: str, source_map_ref: str) -> None:
    """Link a stored source map blob to an asset. Set once, in the same tx that
    marks the asset fetch_ok (capture ingest) — first-wins, so a retry or a later
    same-url batch never clobbers the original map."""
    _set(session, asset_id, {"source_map_ref": source_map_ref})


def set_fetch_failed(session: Session, asset_id: str, error: str) -> None:
    _set(
        session, asset_id,
        {"fetch_status": AssetStatus.FAILED.value, "fetch_error": error[:_ERR_CAP]}
    )


def set_analyze_ok(session: Session, asset_id: str) -> None:
    _set(session, asset_id, {"analyze_status": AssetStatus.OK.value, "analyze_error": None})


def set_analyze_failed(session: Session, asset_id: str, error: str) -> None:
    _set(
        session, asset_id,
        {"analyze_status": AssetStatus.FAILED.value, "analyze_error": error[:_ERR_CAP]}
    )
