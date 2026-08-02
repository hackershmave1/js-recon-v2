"""Engagement/session service — the scope lock + authorization ack (REQ-P3, REQ-C1).

A run may only start under a session that carries a lightweight authorization
acknowledgment (``authorized_by``). A CRAWL additionally needs in-scope hosts —
its egress scope is taken from here, never derived from crawled content (REQ-P2)
— while an UPLOAD needs no scope (S3): a blank scope is allowed, and when a crawl
target is supplied at create time its host seeds the scope so the user need not
retype the domain. Declared entries are validated up front (a bad host is a 400,
not a silently-dropped allow-list entry).

R6 widens this into the Sessions surface: listing a tenant's sessions as cards
(each with its LATEST run's real stats), plus rename / archive / delete. Per-card
stats come from the latest run ONLY: a finding_hash recurs across a session's runs
by design (models.py `uq_finding_run_hash`), so summing runs would double-count.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recon.db.base import admin_session, tenant_session
from recon.db.models import Engagement, EngagementSession, Finding, Run, RunAsset, Tenant
from recon.domain import FindingType
from recon.fetch import egress
from recon.findings.queries import _latest_coverage


class AuthorizationRequired(Exception):
    """A session needs declared scope hosts and an authorization acknowledgment."""


class SessionInvalid(Exception):
    """A mutation carried an invalid value (e.g. an empty rename)."""


@dataclass(frozen=True)
class SessionView:
    id: str
    tenant_id: str
    name: str | None
    scope_hosts: list[str]
    authorization_ack: bool
    created_at: str | None
    engagement_id: str | None
    archived: bool


@dataclass(frozen=True)
class RunRefView:
    run_id: str
    state: str
    created_at: str | None
    started_at: str | None
    ended_at: str | None
    target: str | None


@dataclass(frozen=True)
class SessionSummary:
    """One Sessions-page card (design R6, mockup lines 704-744): the session plus
    its latest run's real, honest stats. ``None`` stats mean "no run yet" or
    "analyze hasn't emitted"; the UI renders those as "—", never a faked number."""

    id: str
    name: str | None
    host: str
    scope_hosts: list[str]
    engagement_id: str | None
    archived: bool
    created_at: str | None
    latest_run: RunRefView | None
    files: int | None
    endpoints: int | None
    secrets: int | None
    coverage_pct: int | None  # attribution coverage %, None until analyze emits


def create_tenant(name: str) -> str:
    """Bootstrap a tenant (the tenant table is not itself tenant-scoped)."""
    with admin_session() as session:
        tenant = Tenant(name=name)
        session.add(tenant)
        session.flush()
        return str(tenant.id)


def _resolve_scope_hosts(scope_hosts: list[str], target: str | None) -> list[str]:
    """Normalize + validate the declared scope, defaulting a blank scope to the
    crawl target's host (S3, REQ-P2/P3).

    Every explicit entry must be a valid host-scope declaration; an invalid one is
    rejected here (a clean create-time 400) rather than silently dropped by the
    egress guard at run time. A blank scope is allowed — an upload session needs
    none — and when a ``target`` is supplied its host seeds the scope so a crawl
    authorizes exactly the domain (and its subdomains) the user typed. A target
    whose host is not itself a valid scope entry (an IP literal, ``localhost``)
    does NOT seed scope; that crawl is refused later by the fail-fast/seed guard.
    """
    cleaned: list[str] = []
    for entry in scope_hosts:
        normalized = egress.normalize_scope_entry(entry)
        if normalized is None:
            raise SessionInvalid(f"invalid scope host: {entry!r}")
        if normalized not in cleaned:
            cleaned.append(normalized)
    if not cleaned and target:
        default_host = egress.normalize_scope_entry(egress.host_of(target))
        if default_host is not None:
            cleaned.append(default_host)
    return cleaned


def create_session(
    tenant_id: str,
    *,
    name: str | None,
    scope_hosts: list[str],
    authorized_by: str,
    engagement_id: str | None = None,
    target: str | None = None,
) -> SessionView:
    """Create an engagement session. ``authorized_by`` is always required (the
    authorization ack). ``scope_hosts`` may be empty (an upload needs no scope);
    when it is and a ``target`` is given, the target's host seeds the scope (S3).
    Invalid scope entries raise :class:`SessionInvalid` (mapped to a 400)."""
    if not authorized_by:
        raise AuthorizationRequired("an authorization acknowledgment is required")
    resolved_scope = _resolve_scope_hosts(scope_hosts, target)
    with tenant_session(tenant_id) as session:
        if engagement_id is not None and session.get(Engagement, engagement_id) is None:
            # RLS confines this lookup to the tenant's own engagements, so a miss means
            # the engagement isn't this tenant's. Reject cleanly: an FK check bypasses
            # RLS, so without this the insert would silently store an inert cross-tenant
            # reference (and mislabel the failure as "unknown tenant").
            raise SessionInvalid("unknown engagement")
        row = EngagementSession(
            tenant_id=tenant_id,
            name=name,
            scope_hosts=resolved_scope,
            authorization_ack=True,
            authorized_by=authorized_by,
            authorized_at=dt.datetime.now(dt.timezone.utc),
            engagement_id=engagement_id,
        )
        session.add(row)
        session.flush()
        session.refresh(row)  # load server-default created_at
        return _view(row)


def get_session(tenant_id: str, session_id: str) -> SessionView | None:
    with tenant_session(tenant_id) as session:
        row = session.get(EngagementSession, session_id)
        return _view(row) if row else None


def list_sessions(
    tenant_id: str, *, include_archived: bool = False
) -> list[SessionSummary]:
    """Every session for the tenant, newest first, each with its latest run's
    stats. RLS confines the result to this tenant; archived sessions are hidden
    unless ``include_archived``."""
    with tenant_session(tenant_id) as session:
        query = select(EngagementSession).order_by(EngagementSession.created_at.desc())
        if not include_archived:
            query = query.where(EngagementSession.archived_at.is_(None))
        rows = session.scalars(query).all()
        return [_summary(session, row) for row in rows]


def list_runs_for_session(
    tenant_id: str, session_id: str
) -> list[RunRefView] | None:
    """A session's runs, newest first, or ``None`` if the session is not visible
    to the tenant (RLS) or does not exist (the HTTP layer maps that to 404)."""
    with tenant_session(tenant_id) as session:
        if session.get(EngagementSession, session_id) is None:
            return None
        rows = session.scalars(
            select(Run)
            .where(Run.session_id == str(session_id))
            .order_by(Run.created_at.desc())
        ).all()
        return [_run_ref(run) for run in rows]


def rename_session(
    tenant_id: str, session_id: str, *, name: str
) -> SessionView | None:
    if not name or not name.strip():
        raise SessionInvalid("a session name is required")
    with tenant_session(tenant_id) as session:
        row = session.get(EngagementSession, session_id)
        if row is None:
            return None
        row.name = name.strip()
        session.flush()
        return _view(row)


def set_session_archived(
    tenant_id: str, session_id: str, *, archived: bool
) -> SessionView | None:
    with tenant_session(tenant_id) as session:
        row = session.get(EngagementSession, session_id)
        if row is None:
            return None
        row.archived_at = dt.datetime.now(dt.timezone.utc) if archived else None
        session.flush()
        return _view(row)


def delete_session(tenant_id: str, session_id: str) -> bool:
    """Hard-delete a session and (FK CASCADE) its runs/findings. Returns False if
    the session is invisible to the tenant or already gone. Object-storage blobs
    are content-addressed and not swept here; a GC pass is future work."""
    with tenant_session(tenant_id) as session:
        row = session.get(EngagementSession, session_id)
        if row is None:
            return False
        session.delete(row)
        return True


# ----------------------------------------------------------------------------- #
# Views + per-card stats (latest run only).
# ----------------------------------------------------------------------------- #


def _view(row: EngagementSession) -> SessionView:
    return SessionView(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        name=row.name,
        scope_hosts=list(row.scope_hosts or []),
        authorization_ack=row.authorization_ack,
        created_at=row.created_at.isoformat() if row.created_at else None,
        engagement_id=str(row.engagement_id) if row.engagement_id else None,
        archived=row.archived_at is not None,
    )


def _run_ref(run: Run) -> RunRefView:
    return RunRefView(
        run_id=str(run.id),
        state=run.state,
        created_at=run.created_at.isoformat() if run.created_at else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        ended_at=run.ended_at.isoformat() if run.ended_at else None,
        target=run.target,
    )


def _summary(db: Session, row: EngagementSession) -> SessionSummary:
    latest = db.scalars(
        select(Run)
        .where(Run.session_id == str(row.id))
        .order_by(Run.created_at.desc())
        .limit(1)
    ).first()
    files = endpoints = secrets = coverage_pct = None
    if latest is not None:
        files, endpoints, secrets, coverage_pct = _run_stats(db, latest)
    # Card label (M3): the rename shows first, then a crawl target, then the
    # declared scope host — uploads have no target, so scope_hosts[0] is the
    # dependable fallback. "—" only if a session somehow declared no scope.
    host = (
        (row.name or "").strip()
        or (latest.target if latest else None)
        or (row.scope_hosts[0] if row.scope_hosts else None)
        or "—"
    )
    return SessionSummary(
        id=str(row.id),
        name=row.name,
        host=host,
        scope_hosts=list(row.scope_hosts or []),
        engagement_id=str(row.engagement_id) if row.engagement_id else None,
        archived=row.archived_at is not None,
        created_at=row.created_at.isoformat() if row.created_at else None,
        latest_run=_run_ref(latest) if latest is not None else None,
        files=files,
        endpoints=endpoints,
        secrets=secrets,
        coverage_pct=coverage_pct,
    )


def _run_stats(
    db: Session, run: Run
) -> tuple[int, int, int, int | None]:
    """(files, endpoints, secrets, coverage_pct) for one run — a cheap read, not
    the heavy findings read-model (§4 fold M4)."""
    run_id = str(run.id)
    # endpoints / secrets: COUNT(*) grouped by finding type, this run only.
    type_counts = {
        row_type: count
        for row_type, count in db.execute(
            select(Finding.type, func.count())
            .where(Finding.run_id == run_id)
            .group_by(Finding.type)
        ).all()
    }
    endpoints = int(type_counts.get(FindingType.ENDPOINT.value, 0))
    secrets = int(type_counts.get(FindingType.SECRET.value, 0))
    # files (§4 fold M1): the run's discovered-asset count for a crawl; 1 for a
    # single-blob upload; else 0. NOT coverage.files (which is per-source-path and
    # double-counts across assets).
    asset_count = (
        db.scalar(
            select(func.count()).select_from(RunAsset).where(RunAsset.run_id == run_id)
        )
        or 0
    )
    if asset_count:
        files = int(asset_count)
    elif run.input_ref:
        files = 1
    else:
        files = 0
    # coverage (§4 fold M2): attribution coverage = attributed / (attributed +
    # unattributed), reusing the multi-asset-correct merge. None until analyze
    # emits — the UI labels this "% attributed", never "% analyzed".
    coverage = _latest_coverage(db, run_id, is_multi_asset=bool(asset_count))
    coverage_pct: int | None = None
    if coverage is not None:
        denom = coverage.attributed + coverage.unattributed
        if denom:
            coverage_pct = int(round(100 * coverage.attributed / denom))
    return files, endpoints, secrets, coverage_pct
