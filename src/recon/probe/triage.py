"""Finding triage / mark-confirmed write path (REQ-P1, REQ-D1).

A verdict is keyed (session_id, finding_hash) so it survives re-runs. The run in
the URL only provides the session scope and the event-log correlation id; the
verdict itself is engagement-scoped. Each change appends a durable ``triage.updated``
run_event (REQ-S3 audit trail)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
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
    """Upsert the verdict for (run's session, finding_hash). ``None`` if the run
    or the finding is invisible to the tenant (RLS) or the finding does not
    exist in this run; ``ValueError`` on an invalid status."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid triage status: {status!r}")

    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)
        finding_exists = session.scalar(
            select(models.Finding.id).where(
                models.Finding.run_id == str(run_id),
                models.Finding.finding_hash == finding_hash,
            )
        )
        if finding_exists is None:
            return None

        insert_stmt = pg_insert(models.FindingTriage).values(
            tenant_id=str(tenant_id), session_id=session_id, finding_hash=finding_hash,
            status=status, note=note, actor=actor,
        )
        upsert = insert_stmt.on_conflict_do_update(
            index_elements=["session_id", "finding_hash"],
            set_={
                "status": status,
                # COALESCE: an omitted note/actor on a status-only update must not
                # clobber a previously stored value.
                "note": func.coalesce(insert_stmt.excluded.note, models.FindingTriage.note),
                "actor": func.coalesce(insert_stmt.excluded.actor, models.FindingTriage.actor),
                "updated_at": func.now(),
            },
        ).returning(models.FindingTriage.updated_at)
        updated_at = session.execute(upsert).scalar_one()
        # Re-read the persisted note/actor: COALESCE may have kept the OLD value,
        # so the API response must reflect the row, not the (possibly None) input.
        persisted_note, persisted_actor = session.execute(
            select(models.FindingTriage.note, models.FindingTriage.actor).where(
                models.FindingTriage.session_id == session_id,
                models.FindingTriage.finding_hash == finding_hash,
            )
        ).one()
        record_event(
            session,
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            event_type="triage.updated",
            payload={"finding_hash": finding_hash, "status": status, "actor": actor},
        )
        return TriageState(
            status=status, note=persisted_note, actor=persisted_actor,
            updated_at=updated_at.isoformat(),
        )
