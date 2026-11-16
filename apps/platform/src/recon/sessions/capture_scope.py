"""Session scope as the extension's Start screen sees it (capture lifecycle).

- ``sessions_for_scope``: "Continue acme.io": the sessions whose scope already holds the entry,
  each with its capture count and largest capture. The largest is shown rather than the latest,
  so a big earlier run stays in view when a later capture of the same target comes back small.
- ``add_scope_entry``: "Add to scope" on the new-site badge.

Matching is EXACT on the normalized entry (``egress.normalize_scope_entry``), never "same last two
labels": that apex guess would merge unrelated targets under a shared suffix (``a.github.io`` and
``b.github.io``)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import EngagementSession, Run
from recon.fetch import egress
from recon.observability import get_logger
from recon.sessions import run_stats

log = get_logger("recon.sessions.capture_scope")

MAX_MATCHES = 10  # each match costs a run_stats scan; the popup lists a handful at most


class InvalidScopeEntry(ValueError):
    """Not a usable host: the API maps it to 422."""


class AdoptRefused(Exception):
    """Adopt did not happen. ``reason`` is one of ``not_found`` (also another tenant's session),
    ``archived``, ``already_adopted`` or ``conflict``; it never carries the existing id."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class WorkspaceMatch:
    """A session started in the workspace (no extension id yet) that Continue could adopt."""

    session_id: str
    engagement_id: str | None
    name: str | None
    runs: int
    last_run_at: str | None


@dataclass(frozen=True)
class Adopted:
    session_id: str
    external_id: str
    engagement_id: str | None


@dataclass(frozen=True)
class CaptureStats:
    run_id: str
    created_at: str | None
    files: int
    endpoints: int


@dataclass(frozen=True)
class ScopeMatch:
    session_id: str
    external_id: str | None
    engagement_id: str | None
    captures: int
    last_capture_at: str | None
    largest: CaptureStats | None  # by endpoints, among the newest run_stats.LIMIT captures


def _normalize(entry: str) -> str | None:
    return egress.normalize_scope_entry(entry, allow_local=get_settings().allow_local_egress)


def sessions_for_scope(tenant_id: str, entry: str) -> list[ScopeMatch]:
    """Sessions whose scope holds ``entry``, the most recently created first."""
    host = _normalize(entry)
    if host is None:
        return []
    with tenant_session(tenant_id) as db:
        sessions = db.scalars(
            select(EngagementSession)
            .where(
                EngagementSession.scope_hosts.contains([host]),
                EngagementSession.archived_at.is_(None),
                # Only extension sessions can be continued: Continue adopts the external id.
                EngagementSession.external_id.is_not(None),
            )
            .order_by(EngagementSession.created_at.desc())
            .limit(MAX_MATCHES)
        ).all()
        matches = []
        for row in sessions:
            # Capture runs only: an adopted workspace session also holds its crawl (or upload) run,
            # which is not a capture ("capture 3" in the popup would otherwise count it).
            runs = db.scalars(
                select(Run)
                .where(
                    Run.session_id == row.id,
                    Run.target.is_(None),
                    Run.input_ref.is_(None),
                    Run.crawl_mode.is_(None),
                )
                .order_by(Run.created_at.desc())
            ).all()
            stats = run_stats.run_stats(db, runs)
            largest = max(
                (
                    CaptureStats(
                        run_id=str(r.id),
                        created_at=r.created_at.isoformat() if r.created_at else None,
                        files=stats[str(r.id)][0],
                        endpoints=stats[str(r.id)][1],
                    )
                    for r in runs
                    if str(r.id) in stats
                ),
                key=lambda c: (c.endpoints, c.files),
                default=None,
            )
            matches.append(
                ScopeMatch(
                    session_id=str(row.id),
                    external_id=row.external_id,
                    engagement_id=str(row.engagement_id) if row.engagement_id else None,
                    captures=len(runs),
                    last_capture_at=runs[0].created_at.isoformat() if runs else None,
                    largest=largest,
                )
            )
        return matches


def workspace_sessions_for_scope(tenant_id: str, entry: str) -> list[WorkspaceMatch]:
    """Sessions of this tenant started in the workspace (no extension id, not archived) whose
    scope holds ``entry``: what Continue may adopt (ADR-0012). No id is shown: there is none."""
    host = _normalize(entry)
    if host is None:
        return []
    with tenant_session(tenant_id) as db:
        rows = db.scalars(
            select(EngagementSession)
            .where(
                EngagementSession.scope_hosts.contains([host]),
                EngagementSession.archived_at.is_(None),
                EngagementSession.external_id.is_(None),
            )
            .order_by(EngagementSession.created_at.desc())
            .limit(MAX_MATCHES)
        ).all()
        out = []
        for row in rows:
            runs = db.scalars(
                select(Run.created_at)
                .where(Run.session_id == row.id)
                .order_by(Run.created_at.desc())
            ).all()
            out.append(
                WorkspaceMatch(
                    session_id=str(row.id),
                    engagement_id=str(row.engagement_id) if row.engagement_id else None,
                    name=row.name,
                    runs=len(runs),
                    last_run_at=runs[0].isoformat() if runs else None,
                )
            )
        return out


def _adopt_once(tenant_id: str, session_id: str) -> Adopted | None:
    """The one write: give a session with NO id a fresh one. Compare-and-set on the row, so of
    several concurrent callers exactly one matches (READ COMMITTED re-checks the WHERE after the
    row lock); nothing but ``external_id`` is ever set."""
    minted = str(uuid.uuid4())
    with tenant_session(tenant_id) as db:
        row = db.execute(
            update(EngagementSession)
            .where(
                EngagementSession.id == session_id,
                EngagementSession.external_id.is_(None),
                EngagementSession.archived_at.is_(None),
            )
            .values(external_id=minted)
            .returning(EngagementSession.id, EngagementSession.engagement_id)
        ).first()
    if row is None:
        return None
    return Adopted(str(row.id), minted, str(row.engagement_id) if row.engagement_id else None)


def adopt_workspace_session(tenant_id: str, session_id: str, *, actor: str) -> Adopted:
    """Continue adopts a workspace session (ADR-0012). Raises :class:`AdoptRefused`."""
    try:
        uuid.UUID(str(session_id))
    except ValueError:
        raise AdoptRefused("not_found") from None
    for _ in range(2):  # the second pass only covers a concurrent un-archive or a uuid clash
        try:
            adopted = _adopt_once(tenant_id, session_id)
        except IntegrityError:
            continue  # a fresh transaction and a fresh id
        if adopted is not None:
            # The audit trail (D48: no session event table). The id is a capability: not logged.
            log.info(
                "session.adopted",
                actor=actor,
                tenant_id=tenant_id,
                session_id=adopted.session_id,
                engagement_id=adopted.engagement_id,
            )
            return adopted
        with tenant_session(tenant_id) as db:
            row = db.get(EngagementSession, session_id)
            if row is None:
                raise AdoptRefused("not_found")
            if row.external_id is not None:
                raise AdoptRefused("already_adopted")
            if row.archived_at is not None:
                raise AdoptRefused("archived")
    raise AdoptRefused("conflict")


def _pruned(scope: list[str]) -> list[str]:
    """The scope without entries another entry already covers (an exact host under its apex)."""
    allow_local = get_settings().allow_local_egress
    return [
        h
        for h in scope
        if not any(
            other != h and egress.host_in_scope(h, [other], allow_local=allow_local)
            for other in scope
        )
    ]


def add_scope_entry(tenant_id: str, session_id: str, entry: str) -> list[str]:
    """Add ``entry`` to the session's scope (no-op when already there); returns the scope.
    Read-time overlays (hosts, hidden findings) pick it up on the next read."""
    host = _normalize(entry)
    if host is None:
        raise InvalidScopeEntry(entry)
    with tenant_session(tenant_id) as db:
        # Locked: two quick "Add to scope" clicks must both land (read-modify-write).
        row = db.get(EngagementSession, session_id, with_for_update=True)
        if row is None:
            raise LookupError(session_id)
        scope = [str(h) for h in row.scope_hosts or []]
        if host not in scope:
            scope.append(host)
            # An entry already covered by another (``cdn.acme.io`` under ``acme.io``) is redundant
            # and would survive removing the broader one, leaving its host still in scope: keep one.
            scope = _pruned(scope)
            row.scope_hosts = scope
        return scope


def remove_scope_entry(tenant_id: str, session_id: str, entry: str) -> list[str]:
    """Remove ``entry`` from the session's scope (no-op when absent); returns the scope.

    A read-time rule, never a deletion: the host's files and findings are HIDDEN under "out of
    scope" (recon.findings.visibility, recon.probe.sources) and return when the host is added back.
    Deleting content is a separate, explicit action (recon.quarantine.scope_view)."""
    host = _normalize(entry)
    if host is None:
        raise InvalidScopeEntry(entry)
    with tenant_session(tenant_id) as db:
        row = db.get(EngagementSession, session_id, with_for_update=True)
        if row is None:
            raise LookupError(session_id)
        scope = [str(h) for h in row.scope_hosts or []]
        if host in scope:
            scope = [h for h in scope if h != host]
            row.scope_hosts = scope
        return scope
