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

from sqlalchemy import func, select

from recon.auth.passwords import hash_password, verify_password
from recon.db.base import admin_session
from recon.db.models import AppUser, Tenant


@dataclass(frozen=True)
class Principal:
    """The authenticated identity: who, in which tenant, with what role."""

    user_id: str
    tenant_id: str
    role: str


# A fixed valid bcrypt hash so authenticate() always runs a compare — even when the user
# is missing or ambiguous — equalizing response time so a timing side-channel can't
# enumerate valid operator usernames. The value is irrelevant; it just can't match.
_DUMMY_HASH = hash_password("recon-timing-equalizer-not-a-real-password")


def normalize_username(username: str) -> str:
    """Canonical form for a login identity: trimmed + lowercased.

    Usernames (stored in ``app_user.email``) are case-INSENSITIVE — "Admin",
    "admin", and "  ADMIN " are the same operator. Normalizing on BOTH the seed
    write and the login read means the lookup (``func.lower(email) == normalized``)
    matches however a row was stored, and newly seeded users are stored canonically.
    A cross-case duplicate within one tenant therefore logs in as genuinely ambiguous
    (``authenticate`` fails closed), never as two distinct identities.
    """
    return username.strip().lower()


def authenticate(username: str, password: str) -> Principal | None:
    """Verify credentials, returning the :class:`Principal` or ``None``.

    ``None`` covers every failure — unknown user, bad password, or an ambiguous
    username — and the caller must not distinguish them to the client (no user
    enumeration). ``app_user`` is UNIQUE only per (tenant, email), so a username is
    not globally unique; a cross-tenant duplicate is genuinely ambiguous (login has
    no workspace selector) and fails CLOSED as a plain "invalid", never revealing that
    the name exists in >1 tenant. DEBT: multi-tenant login needs a workspace selector
    or a globally-unique login identity before a second tenant reuses a username.
    """
    normalized = normalize_username(username)
    with admin_session() as session:
        # Case-insensitive lookup (func.lower) so "Admin"/"admin" resolve to one
        # identity, matching however the row was stored; seed writes are normalized too.
        users = (
            session.execute(select(AppUser).where(func.lower(AppUser.email) == normalized))
            .scalars()
            .all()
        )
    if len(users) != 1:
        # Missing or ambiguous: still spend a bcrypt compare (constant-ish time) so the
        # response time can't distinguish a real username, then fail generically.
        verify_password(password, _DUMMY_HASH)
        return None
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
        normalized = normalize_username(username)
        user = session.execute(
            select(AppUser).where(AppUser.tenant_id == tid, func.lower(AppUser.email) == normalized)
        ).scalar_one_or_none()
        hashed = hash_password(password)
        if user is None:
            user = AppUser(tenant_id=tid, email=normalized, role=role, password_hash=hashed)
            session.add(user)
            session.flush()
        else:
            user.password_hash = hashed
            user.role = role
        return str(user.id)
