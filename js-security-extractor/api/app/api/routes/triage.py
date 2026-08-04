"""Finding triage status endpoints (UI-002 Phase 2).

Persists per-finding analyst triage state for the RECON Workspace Findings view.
A finding is identified by an opaque ``fingerprint`` computed client-side
(``web/src/transforms.js`` :: ``fingerprintOf``) from its type/value/file/line, so
the same finding keeps its status across reloads and re-analysis.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ...db import get_db
from ...models import FindingStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["triage"])

VALID_STATUSES = {"new", "reviewed", "confirmed", "false_positive"}


class FindingStatusUpdate(BaseModel):
    """Request body for setting a single finding's triage status."""

    fingerprint: str = Field(..., min_length=1, max_length=64, description="Stable client-computed finding identity")
    status: str = Field(..., description="new | reviewed | confirmed | false_positive")


@router.get("/sessions/{session_id}/finding-status")
def get_finding_statuses(session_id: str, db: DbSession = Depends(get_db)) -> dict:
    """Return all triage statuses for a session as ``{fingerprint: status}``.

    Defaults are implicit: any fingerprint not present is treated as ``new`` by
    the client, so only triaged findings are stored.
    """
    rows = db.query(FindingStatus).filter(FindingStatus.session_id == session_id).all()
    return {"statuses": {row.fingerprint: row.status for row in rows}}


@router.put("/sessions/{session_id}/finding-status")
def set_finding_status(
    session_id: str,
    update: FindingStatusUpdate,
    db: DbSession = Depends(get_db),
) -> dict:
    """Upsert the triage status for one finding in a session.

    Session existence is intentionally not required — a status may be recorded for
    any session id. The upsert is race-safe across the unique constraint: on a
    concurrent insert collision we roll back and update the now-existing row, so
    the same code path works on both SQLite (tests) and Postgres (prod) without a
    dialect-specific ``ON CONFLICT``.
    """
    if update.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{update.status}'. Expected one of {sorted(VALID_STATUSES)}.",
        )

    row = (
        db.query(FindingStatus)
        .filter(
            FindingStatus.session_id == session_id,
            FindingStatus.fingerprint == update.fingerprint,
        )
        .first()
    )
    if row:
        row.status = update.status
    else:
        row = FindingStatus(
            session_id=session_id,
            fingerprint=update.fingerprint,
            status=update.status,
        )
        db.add(row)

    try:
        db.commit()
    except IntegrityError:
        # A concurrent PUT inserted the same (session_id, fingerprint) first.
        db.rollback()
        row = (
            db.query(FindingStatus)
            .filter(
                FindingStatus.session_id == session_id,
                FindingStatus.fingerprint == update.fingerprint,
            )
            .first()
        )
        if row is None:
            # The collision row was removed between the failed insert and this
            # re-query (no delete path exists today, but don't dereference None).
            row = FindingStatus(
                session_id=session_id,
                fingerprint=update.fingerprint,
                status=update.status,
            )
            db.add(row)
        else:
            row.status = update.status
        db.commit()

    db.refresh(row)
    return {
        "session_id": session_id,
        "fingerprint": row.fingerprint,
        "status": row.status,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
