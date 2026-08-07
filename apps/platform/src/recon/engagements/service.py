"""Engagement service — the scope umbrella that groups sessions (design R6).

An engagement is analyst-facing organizational metadata: a name plus an in/out-of
-scope domain statement, surfaced in the UI's engagement switcher. It does NOT
enforce egress scope — a run's scope still comes from its session's ``scope_hosts``
(REQ-P2), never derived from an engagement here. Tenant-scoped via ``tenant_session``
(RLS), so an engagement created under one tenant is invisible to another.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from recon.db.base import tenant_session
from recon.db.models import Engagement


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
        rows = session.scalars(select(Engagement).order_by(Engagement.created_at.desc())).all()
        return [_view(row) for row in rows]


def _view(row: Engagement) -> EngagementView:
    return EngagementView(
        id=str(row.id),
        name=row.name,
        in_scope_domains=list(row.in_scope_domains or []),
        out_of_scope_domains=list(row.out_of_scope_domains or []),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )
