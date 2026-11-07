"""Session scope as the extension's Start screen sees it (capture lifecycle).

- ``sessions_for_scope``: "Continue acme.io": the sessions whose scope already holds the entry,
  each with its capture count and largest capture. The largest is shown rather than the latest,
  so a big earlier run stays in view when a later capture of the same target comes back small.
- ``add_scope_entry``: "Add to scope" on the new-site badge.

Matching is EXACT on the normalized entry (``egress.normalize_scope_entry``), never "same last two
labels": that apex guess would merge unrelated targets under a shared suffix (``a.github.io`` and
``b.github.io``)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import EngagementSession, Run
from recon.fetch import egress
from recon.sessions import run_stats

MAX_MATCHES = 10  # each match costs a run_stats scan; the popup lists a handful at most


class InvalidScopeEntry(ValueError):
    """Not a usable host: the API maps it to 422."""


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
            runs = db.scalars(
                select(Run).where(Run.session_id == row.id).order_by(Run.created_at.desc())
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
            row.scope_hosts = scope
        return scope
