"""REQ-C2 manual base-URL rules — the write path (spec §6).

Validate + persist a rule into the run's session, then reclassify so the shadow
verdicts stay in sync. Read-time consumers (reconstruct/export) reflect a rule
live regardless. `None` when the run is invisible to the tenant (RLS) -> the
router maps that to 404.

Two-transaction note (spec §6, gate N5): the rule is persisted in one
tenant_session, then reclassify_run opens its own. Harmless — reconstruct/export
reflect rules live and reclassify is idempotent and re-runnable.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from recon.db import models
from recon.db.base import tenant_session
from recon.findings.base_url import validate_base_url
from recon.spec.service import reclassify_run


def _as_dict(row: models.SessionBaseUrl) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "path_prefix": row.path_prefix,
        "finding_hashes": list(row.finding_hashes or ()),
        "base_url": row.base_url,
        "actor": row.actor,
    }


def add_rule(
    tenant_id: str,
    run_id: str,
    *,
    kind: str,
    base_url: str,
    path_prefix: str | None = None,
    finding_hashes: list[str] | None = None,
    actor: str | None = None,
) -> dict[str, Any] | None:
    """Returns ``{"rule": <rule dict>, "summary": <asdict(SpecSummary) | None>}``,
    or ``None`` if `run_id` is invisible to `tenant_id` (RLS)."""
    validate_base_url(base_url)  # InvalidBaseUrl -> router 422
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)
        values = {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "kind": kind,
            "path_prefix": path_prefix,
            "finding_hashes": finding_hashes,
            "base_url": base_url,
            "actor": actor,
        }
        if kind == "prefix":
            stmt = (
                pg_insert(models.SessionBaseUrl)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["session_id", "path_prefix"],
                    set_={"base_url": base_url, "actor": actor, "updated_at": func.now()},
                )
                .returning(models.SessionBaseUrl)
            )
        else:
            stmt = (
                pg_insert(models.SessionBaseUrl).values(**values).returning(models.SessionBaseUrl)
            )
        row = session.scalars(stmt).one()
        result = _as_dict(row)
    run_summary = reclassify_run(tenant_id, run_id)  # own transaction (gate N5)
    return {"rule": result, "summary": asdict(run_summary) if run_summary else None}


def list_rules(tenant_id: str, run_id: str) -> list[dict[str, Any]] | None:
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        rows = session.scalars(
            select(models.SessionBaseUrl)
            .where(models.SessionBaseUrl.session_id == str(run.session_id))
            .order_by(models.SessionBaseUrl.created_at)
        ).all()
        return [_as_dict(row) for row in rows]


def delete_rule(tenant_id: str, run_id: str, rule_id: str) -> bool | None:
    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        return False
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        result = session.execute(
            delete(models.SessionBaseUrl).where(
                models.SessionBaseUrl.id == rule_uuid,
                models.SessionBaseUrl.session_id == str(run.session_id),
            )
        )
        deleted = cast(CursorResult[Any], result).rowcount > 0
    if deleted:
        reclassify_run(tenant_id, run_id)
    return deleted
