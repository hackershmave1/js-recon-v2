"""Finding triage / mark-confirmed write path (REQ-P1, REQ-D1).

A verdict is keyed (session_id, finding_hash) so it survives re-runs. The run in
the URL only provides the session scope and the event-log correlation id; the
verdict itself is engagement-scoped. Each change appends a durable ``triage.updated``
run_event (REQ-S3 audit trail)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from recon.db import models
from recon.db.base import tenant_session
from recon.events.log import record_event

VALID_STATUSES: frozenset[str] = frozenset({"open", "confirmed", "dismissed"})


@dataclass(frozen=True)
class TriageState:
    status: str
    note: str | None
    actor: str | None
    updated_at: str


def set_triage_for_run(
    tenant_id: str,
    run_id: str,
    finding_hash: str,
    *,
    status: str,
    note: str | None = None,
    actor: str | None = None,
) -> TriageState | None:
    """Upsert the verdict for (run's session, finding_hash). ``None`` if the run is
    invisible to the tenant (RLS); ``ValueError`` on an invalid status."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid triage status: {status!r}")

    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)

        upsert = (
            pg_insert(models.FindingTriage)
            .values(
                tenant_id=str(tenant_id),
                session_id=session_id,
                finding_hash=finding_hash,
                status=status,
                note=note,
                actor=actor,
            )
            .on_conflict_do_update(
                index_elements=["session_id", "finding_hash"],
                set_={"status": status, "note": note, "actor": actor, "updated_at": func.now()},
            )
            .returning(models.FindingTriage.updated_at)
        )
        updated_at = session.execute(upsert).scalar_one()
        record_event(
            session,
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            event_type="triage.updated",
            payload={"finding_hash": finding_hash, "status": status, "actor": actor},
        )
        return TriageState(status=status, note=note, actor=actor, updated_at=updated_at.isoformat())
