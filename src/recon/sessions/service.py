"""Engagement/session service — the scope lock + authorization ack (REQ-P3, REQ-C1).

A run may only start under a session that declares its in-scope hosts and carries
a lightweight authorization acknowledgment. Egress scope is taken from here and
never derived from crawled content (REQ-P2).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from recon.db.base import admin_session, tenant_session
from recon.db.models import EngagementSession, Tenant


class AuthorizationRequired(Exception):
    """A session needs declared scope hosts and an authorization acknowledgment."""


@dataclass(frozen=True)
class SessionView:
    id: str
    tenant_id: str
    name: str | None
    scope_hosts: list[str]
    authorization_ack: bool


def create_tenant(name: str) -> str:
    """Bootstrap a tenant (the tenant table is not itself tenant-scoped)."""
    with admin_session() as session:
        tenant = Tenant(name=name)
        session.add(tenant)
        session.flush()
        return str(tenant.id)


def create_session(
    tenant_id: str,
    *,
    name: str | None,
    scope_hosts: list[str],
    authorized_by: str,
) -> SessionView:
    if not scope_hosts:
        raise AuthorizationRequired("at least one in-scope host must be declared")
    if not authorized_by:
        raise AuthorizationRequired("an authorization acknowledgment is required")
    with tenant_session(tenant_id) as session:
        row = EngagementSession(
            tenant_id=tenant_id,
            name=name,
            scope_hosts=scope_hosts,
            authorization_ack=True,
            authorized_by=authorized_by,
            authorized_at=dt.datetime.now(dt.timezone.utc),
        )
        session.add(row)
        session.flush()
        return _view(row)


def get_session(tenant_id: str, session_id: str) -> SessionView | None:
    with tenant_session(tenant_id) as session:
        row = session.get(EngagementSession, session_id)
        return _view(row) if row else None


def _view(row: EngagementSession) -> SessionView:
    return SessionView(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        name=row.name,
        scope_hosts=list(row.scope_hosts or []),
        authorization_ack=row.authorization_ack,
    )
