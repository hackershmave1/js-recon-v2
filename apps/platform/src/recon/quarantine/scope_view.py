"""The Hosts page's scope panel (ADR-0010, S3): what the session's scope is, which hosts await a
decision, which are ignored, which are HIDDEN because they were removed from the scope, and the
explicit delete of a removed host's content.

Removing a host from the scope only HIDES its files and findings (read-time, reversible: the
visibility rules treat a host outside the scope as "out of scope"); adding it back brings them
back. Deleting content is a different action that needs the host to be out of scope first and can
not be undone: it removes the files (and their blobs, reference-counted), the sightings in them and
any finding left with none.
"""

from __future__ import annotations

import contextlib

from sqlalchemy import delete, func, select

from recon import storage
from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import HostDecisionState
from recon.fetch import egress
from recon.findings import visibility
from recon.quarantine import blobs, service


class HostInScope(Exception):
    """Content of a host that is still in the scope cannot be deleted: remove it from the scope first."""


def _first_party(session, session_id: str) -> list[str]:
    row = session.get(models.EngagementSession, session_id)
    if row is None:
        return []
    entries = [str(h) for h in row.scope_hosts or []]
    domains = session.scalar(
        select(models.Engagement.in_scope_domains)
        .join(
            models.EngagementSession, models.EngagementSession.engagement_id == models.Engagement.id
        )
        .where(models.EngagementSession.id == session_id)
    )
    entries += [str(d) for d in domains or []]
    return visibility.expand_scope_for_display(
        entries, allow_local=get_settings().allow_local_egress
    )


def overview(tenant_id: str, session_id: str) -> dict | None:
    """Scope, undecided and ignored hosts, and the hidden (removed-from-scope) hosts of a session,
    or ``None`` when the session is not visible to the tenant."""
    allow_local = get_settings().allow_local_egress
    with tenant_session(tenant_id) as session:
        row = session.get(models.EngagementSession, session_id)
        if row is None:
            return None
        scope_hosts = [str(h) for h in row.scope_hosts or []]
        first_party = _first_party(session, session_id)
        assets = session.execute(
            select(models.RunAsset.id, models.RunAsset.url)
            .join(models.Run, models.Run.id == models.RunAsset.run_id)
            .where(models.Run.session_id == session_id)
        ).all()
        files_by_host: dict[str, list] = {}
        for asset_id, url in assets:
            host = visibility._static_host(url)
            if (
                host
                and first_party
                and not egress.host_in_scope(host, first_party, allow_local=allow_local)
            ):
                files_by_host.setdefault(host, []).append(asset_id)
        findings_by_host: dict[str, int] = {}
        for host, ids in files_by_host.items():
            findings_by_host[host] = int(
                session.scalar(
                    select(func.count(func.distinct(models.FindingOccurrence.finding_id))).where(
                        models.FindingOccurrence.run_asset_id.in_(ids)
                    )
                )
                or 0
            )
    return {
        "scope_hosts": scope_hosts,
        "undecided": service.undecided_hosts(tenant_id=tenant_id, session_id=session_id),
        "ignored": service.ignored_hosts(tenant_id=tenant_id, session_id=session_id),
        "hidden": sorted(
            (
                {"host": host, "files": len(ids), "findings": findings_by_host.get(host, 0)}
                for host, ids in files_by_host.items()
            ),
            key=lambda h: h["host"],
        ),
    }


def forget_ignored(tenant_id: str, session_id: str, host: str) -> bool:
    """Take ``host`` off the ignore list (the researcher added it to the scope instead). Returns
    whether it was ignored."""
    host = service.normalize_host(host)
    with tenant_session(tenant_id) as session:
        result = session.execute(
            delete(models.HostDecision).where(
                models.HostDecision.session_id == session_id,
                models.HostDecision.host == host,
                models.HostDecision.state == HostDecisionState.IGNORED.value,
            )
        )
        return bool(result.rowcount)


def delete_host_content(tenant_id: str, session_id: str, host: str) -> dict:
    """The explicit, irreversible delete of a REMOVED host's files, their sightings and the findings
    left with none. Refuses a host still in the scope. Blobs go only when no other file of the run
    uses that digest (reference-counted)."""
    host = service.normalize_host(host)
    allow_local = get_settings().allow_local_egress
    keys: list[str] = []
    deleted_files = deleted_findings = 0
    with tenant_session(tenant_id) as session:
        first_party = _first_party(session, session_id)
        if first_party and egress.host_in_scope(host, first_party, allow_local=allow_local):
            raise HostInScope(host)
        rows = [
            r
            for r in session.scalars(
                select(models.RunAsset)
                .join(models.Run, models.Run.id == models.RunAsset.run_id)
                .where(models.Run.session_id == session_id)
            )
            if visibility._static_host(r.url) == host
        ]
        if not rows:
            return {"files": 0, "findings": 0}
        ids = [r.id for r in rows]
        touched = {
            f
            for (f,) in session.execute(
                select(models.FindingOccurrence.finding_id).where(
                    models.FindingOccurrence.run_asset_id.in_(ids)
                )
            )
        }
        session.execute(
            delete(models.FindingOccurrence).where(models.FindingOccurrence.run_asset_id.in_(ids))
        )
        if touched:
            orphaned = [
                f
                for f in touched
                if not session.scalar(
                    select(models.FindingOccurrence.id)
                    .where(models.FindingOccurrence.finding_id == f)
                    .limit(1)
                )
            ]
            if orphaned:
                session.execute(delete(models.Finding).where(models.Finding.id.in_(orphaned)))
                deleted_findings = len(orphaned)
        by_run: dict[str, list[models.RunAsset]] = {}
        for r in rows:
            by_run.setdefault(str(r.run_id), []).append(r)
        session.execute(delete(models.RunAsset).where(models.RunAsset.id.in_(ids)))
        deleted_files = len(rows)
        for run_id, run_rows in by_run.items():
            keys += blobs.unreferenced_keys(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                input_refs=[r.input_ref for r in run_rows],
                map_refs=[r.source_map_ref for r in run_rows],
            )
    with contextlib.suppress(Exception):  # rows are gone; a leftover blob is purged with the run
        storage.delete_keys(keys)
    return {"files": deleted_files, "findings": deleted_findings}
