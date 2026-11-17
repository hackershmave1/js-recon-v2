"""Engagement service — the scope umbrella that groups sessions (design R6).

An engagement is analyst-facing organizational metadata: a name plus an in/out-of
-scope domain statement, surfaced in the UI's engagement switcher. It does NOT
enforce egress scope — a run's scope still comes from its session's ``scope_hosts``
(REQ-P2), never derived from an engagement here. Tenant-scoped via ``tenant_session``
(RLS), so an engagement created under one tenant is invisible to another. Deleting
one detaches its sessions (they become standalone) and never deletes them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update

from recon.db.base import tenant_session
from recon.db.models import Engagement, EngagementSession
from recon.observability import get_logger

_logger = get_logger(__name__)


class EngagementInvalid(Exception):
    """An engagement needs a non-empty name."""


@dataclass(frozen=True)
class EngagementView:
    id: str
    name: str
    in_scope_domains: list[str]
    out_of_scope_domains: list[str]
    created_at: str
    updated_at: str
    # Sessions it groups, archived ones included: what a delete would detach (#262).
    session_count: int = 0


def create_engagement(
    tenant_id: str,
    *,
    name: str,
    in_scope_domains: list[str],
    out_of_scope_domains: list[str],
) -> EngagementView:
    if not name or not name.strip():
        raise EngagementInvalid("an engagement name is required")
    with tenant_session(tenant_id) as session:
        row = Engagement(
            tenant_id=tenant_id,
            name=name.strip(),
            in_scope_domains=[d.strip() for d in in_scope_domains if d.strip()],
            out_of_scope_domains=[d.strip() for d in out_of_scope_domains if d.strip()],
        )
        session.add(row)
        session.flush()
        session.refresh(row)  # load server-default created_at/updated_at
        return _view(row)


def list_engagements(tenant_id: str) -> list[EngagementView]:
    with tenant_session(tenant_id) as session:
        counts = (
            select(EngagementSession.engagement_id, func.count().label("n"))
            .where(EngagementSession.engagement_id.is_not(None))
            .group_by(EngagementSession.engagement_id)
            .subquery()
        )
        rows = session.execute(
            select(Engagement, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.engagement_id == Engagement.id)
            .order_by(Engagement.created_at.desc())
        ).all()
        return [_view(row, session_count=count) for row, count in rows]


def delete_engagement(
    tenant_id: str, engagement_id: str, *, actor: str | None = None
) -> int | None:
    """Delete an engagement and DETACH its sessions (issue #262): every session it grouped
    goes back to "No engagement"; no session, run or finding is deleted. Returns how many
    sessions were detached, or ``None`` when the id is malformed or not this tenant's (RLS
    hides other tenants' rows, so both read as "not found").

    Detaching is the one change a session's engagement binding ever gets: the binding is
    never re-pointed at a different engagement, it only ends when its engagement does. The
    FK is ``ON DELETE SET NULL`` (migration 0009) so the database would detach anyway; the
    explicit UPDATE is what lets us count, and it runs under the tenant's RLS like the read.

    The engagement row is locked first (``FOR UPDATE``) so a capture creating a session
    bound to it concurrently either commits before the UPDATE's snapshot (and is counted +
    detached) or fails its FK check after our commit, which capture_router's
    ``_create_capture_session`` turns into an unbound session — the count is never short."""
    try:
        engagement_uuid = uuid.UUID(str(engagement_id))
    except ValueError:
        return None
    with tenant_session(tenant_id) as session:
        row = session.scalars(
            select(Engagement).where(Engagement.id == engagement_uuid).with_for_update()
        ).first()
        if row is None:
            return None
        detached_ids = session.scalars(
            update(EngagementSession)
            .where(EngagementSession.engagement_id == engagement_uuid)
            .values(engagement_id=None)
            .returning(EngagementSession.id)
        ).all()
        # A Core delete: the ORM's session.delete would first lazy-load the (now empty)
        # Engagement.sessions relationship to null it out, a wasted round trip.
        session.execute(delete(Engagement).where(Engagement.id == engagement_uuid))
        # Structured log IS the audit record: the row is gone after the commit (D48).
        _logger.info(
            "engagement.deleted",
            actor=actor,
            tenant_id=tenant_id,
            engagement_id=str(engagement_uuid),
            detached_sessions=len(detached_ids),
        )
        return len(detached_ids)


def _view(row: Engagement, *, session_count: int = 0) -> EngagementView:
    return EngagementView(
        id=str(row.id),
        name=row.name,
        in_scope_domains=list(row.in_scope_domains or []),
        out_of_scope_domains=list(row.out_of_scope_domains or []),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        session_count=int(session_count),
    )
