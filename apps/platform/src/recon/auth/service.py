"""Auth service: authenticate a login, and the idempotent dev-admin seed.

Login is special: it resolves a user BEFORE any tenant context exists (we don't
know the tenant until we've found the user). So the lookup uses ``admin_session()``
— the privileged, RLS-bypassing connection — which is the one legitimate
cross-tenant read on the platform. It is confined to this module and never reached
from a tenant-scoped request path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from recon.auth.passwords import hash_password, verify_password
from recon.db.base import admin_session
from recon.db.models import AppUser, Tenant


@dataclass(frozen=True)
class Principal:
    """The authenticated identity: who, in which tenant, with what role."""

    user_id: str
    tenant_id: str
    role: str


class AmbiguousUser(Exception):
    """The login name exists in more than one tenant.

    ``app_user`` is UNIQUE only per (tenant, email), so a username is not globally
    unique. Login has no workspace selector, so a cross-tenant duplicate is a
    genuinely ambiguous identity — we fail CLOSED rather than authenticate into an
    arbitrary tenant. DEBT: multi-tenant login needs a workspace selector or a
    globally-unique login identity before a second tenant gets the same username.
    """


def authenticate(username: str, password: str) -> Principal | None:
    """Verify credentials, returning the :class:`Principal` or ``None``.

    ``None`` = no such user or a bad password (the caller must not distinguish the
    two to the client). Raises :class:`AmbiguousUser` when the username is not
    unique across tenants.
    """
    with admin_session() as session:
        users = session.execute(select(AppUser).where(AppUser.email == username)).scalars().all()
    if not users:
        return None
    if len(users) > 1:
        raise AmbiguousUser(username)
    user = users[0]
    if not verify_password(password, user.password_hash):
        return None
    return Principal(user_id=str(user.id), tenant_id=str(user.tenant_id), role=user.role)


def tenant_name(tenant_id: str) -> str | None:
    """The display name of a tenant, for the login/``/me`` response. Uses the admin
    connection because the caller is resolving their OWN just-verified tenant and no
    tenant context is set yet; returns ``None`` if the tenant vanished."""
    with admin_session() as session:
        tenant = session.get(Tenant, uuid.UUID(tenant_id))
        return tenant.name if tenant is not None else None


def seed_admin(
    *,
    username: str,
    password: str,
    tenant_id: str,
    tenant_name: str,
    role: str = "admin",
) -> str:
    """Idempotently ensure a fixed-id tenant and a login user exist; return the user id.

    Cannot go through ``sessions.service.create_tenant`` — that mints a RANDOM tenant
    id, but the seed must bind to a SPECIFIC tenant (e.g. the operator's existing
    workspace), and must be safe to re-run. So it upserts by natural key inside
    ``admin_session()`` (RLS-bypass, required to write ``tenant``/``app_user`` without
    a tenant context) and refreshes the password + role on re-run (dev convenience).
    """
    tid = uuid.UUID(tenant_id)
    with admin_session() as session:
        tenant = session.get(Tenant, tid)
        if tenant is None:
            session.add(Tenant(id=tid, name=tenant_name))
            session.flush()
        user = session.execute(
            select(AppUser).where(AppUser.tenant_id == tid, AppUser.email == username)
        ).scalar_one_or_none()
        hashed = hash_password(password)
        if user is None:
            user = AppUser(tenant_id=tid, email=username, role=role, password_hash=hashed)
            session.add(user)
            session.flush()
        else:
            user.password_hash = hashed
            user.role = role
        return str(user.id)
