"""REQ-C2 wrapper-teaching — the write path (spec §6).

Validate + persist a taught wrapper callee into the run's session, then re-extract
the run's stored source so its wrapper calls surface as findings. `None` when the
run is invisible to the tenant (RLS) -> the router maps that to 404.

Two-transaction note (mirrors base_url_service): the rule is persisted in one
tenant_session, then reextract_run opens its own. Harmless — the outbox is
idempotent and the persisted rule is committed before the re-extract reads.
`DELETE` only removes the rule and does NOT re-extract: per spec §8 the outbox
cannot retract already-persisted wrapper findings, so a re-extract on delete would
be a pure no-op.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from recon.db import models
from recon.db.base import tenant_session
from recon.findings import queries, reextract
from recon.findings.wrappers import validate_callee


def _as_dict(row: models.SessionWrapper) -> dict[str, Any]:
    return {"id": str(row.id), "callee": row.callee, "actor": row.actor}


def add_rule(
    tenant_id: str, run_id: str, *, callee: str, actor: str | None = None
) -> dict[str, Any] | None:
    """Persist a wrapper callee (upsert on ``(session_id, callee)``) and re-extract
    the run. Returns ``{"rule": <dict>, "recovered": <int rows written>}``, or
    ``None`` if `run_id` is invisible to `tenant_id` (RLS). ``InvalidWrapperCallee``
    propagates uncaught -> the router maps it to 422."""
    validate_callee(callee)
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)
        stmt = (
            pg_insert(models.SessionWrapper)
            .values(tenant_id=tenant_id, session_id=session_id, callee=callee, actor=actor)
            .on_conflict_do_update(
                index_elements=["session_id", "callee"],
                set_={"actor": actor, "updated_at": func.now()},
            )
            .returning(models.SessionWrapper)
        )
        row = session.scalars(stmt).one()
        rule = _as_dict(row)
        rules = queries.wrapper_rules_in_session(session, session_id)
    recovered = reextract.reextract_run(tenant_id, run_id, rules)  # own transaction(s)
    return {"rule": rule, "recovered": recovered or 0}


def list_rules(tenant_id: str, run_id: str) -> list[dict[str, Any]] | None:
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        rows = session.scalars(
            select(models.SessionWrapper)
            .where(models.SessionWrapper.session_id == str(run.session_id))
            .order_by(models.SessionWrapper.created_at)
        ).all()
        return [_as_dict(row) for row in rows]


def delete_rule(tenant_id: str, run_id: str, rule_id: str) -> bool | None:
    """Remove the rule so future runs / re-extracts stop recognizing the callee.
    Does NOT retract already-persisted wrapper findings (spec §8), so it does not
    re-extract."""
    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        return False
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        result = session.execute(
            delete(models.SessionWrapper).where(
                models.SessionWrapper.id == rule_uuid,
                models.SessionWrapper.session_id == str(run.session_id),
            )
        )
        return cast(CursorResult[Any], result).rowcount > 0
