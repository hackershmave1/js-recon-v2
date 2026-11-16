"""Quarantine service (ADR-0010): admit scripts from an undecided host, then Add, Ignore or expire.

Four operations, each ONE transaction, each safe against the others:

* :func:`admit` stores a batch of an undecided host's scripts apart from the run, enforcing the
  session's ignore list and the caps. It takes a SHARE lock on the host's decision row, so an
  Ignore that commits first is seen (and refused), and an Ignore that comes later waits for the
  admit and then deletes what it stored. A replayed batch after Ignore therefore cannot re-store.
* :func:`decide` is the researcher's Add or Ignore, a compare-and-set on the decision row
  (``undecided -> added | ignored``). Of two racing decisions exactly one wins; the loser gets
  ``conflict`` with the winner's state. The same decision repeated is an idempotent success.
* :func:`sweep` expires hosts undecided for ``quarantine_expiry_days`` (``undecided -> expired``,
  content deleted), by the same compare-and-set.
* Deleting content is reference-counted within the run (:mod:`recon.quarantine.blobs`).

Add after the capture has ended: promotion inserts the rows into the SAME run whatever its state.
A run that can still take a pass is scheduled for one (``analysis: "scheduled"``); a finished run
rests where it is and the caller re-runs it (``analysis: "needs_rerun"``), so a sealed run never
changes silently.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from recon import storage
from recon.config import get_settings
from recon.db import models
from recon.db.base import admin_session, tenant_session
from recon.domain import TERMINAL_STATES, HostDecisionState, RunState
from recon.observability import get_logger
from recon.quarantine import blobs
from recon.runs import capture_lifecycle, captured_versions

log = get_logger("recon.quarantine")

# Outcomes of one file in a batch.
STORED = "stored"
DUPLICATE = "duplicate"  # these bytes are already quarantined for this run and url (a retry)
IGNORED = "ignored"  # the host is on the session's ignore list: never stored
IN_SCOPE = "in_scope"  # the host was already added: the caller uses the normal capture path
INVALID = "invalid"  # empty or over the per-file size cap
CAP_HOSTS = "cap_hosts"
CAP_HOST_FILES = "cap_host_files"
CAP_HOST_BYTES = "cap_host_bytes"
CAP_SESSION_BYTES = "cap_session_bytes"

_MAX_HOST_LEN = 253


class QuarantineError(Exception):
    """The request names a session, run or host that does not exist for this tenant."""


@dataclass(frozen=True)
class FileIn:
    url: str
    data: bytes


@dataclass(frozen=True)
class FileResult:
    url: str
    outcome: str


@dataclass
class DecisionResult:
    status: str  # "ok" | "conflict" | "not_found"
    state: str | None = None  # the decision's state after the call (the winner's, on conflict)
    promoted: int = 0
    deleted: int = 0
    analysis: dict[str, str] = field(default_factory=dict)  # run id -> scheduled | needs_rerun


def normalize_host(host: str) -> str:
    cleaned = host.strip().lower().rstrip(".")
    if not cleaned or len(cleaned) > _MAX_HOST_LEN or any(c in cleaned for c in "/ \t\r\n@"):
        raise QuarantineError(f"not a host: {host!r}")
    return cleaned


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def admit(
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
    host: str,
    files: list[FileIn],
) -> list[FileResult]:
    """Store ``files`` (all from ``host``) in quarantine, or report why each was not."""
    host = normalize_host(host)
    settings = get_settings()
    now = _now()
    expires = now + dt.timedelta(days=settings.quarantine_expiry_days)
    results: list[FileResult] = []
    with tenant_session(tenant_id) as session:
        owner = session.scalar(select(models.Run.session_id).where(models.Run.id == run_id))
        if owner is None or str(owner) != str(session_id):
            raise QuarantineError("run does not belong to the session")
        decision = _lock_decision_for_share(session, tenant_id, session_id, host)
        if decision is None:
            if (
                _undecided_host_count(session, session_id)
                >= settings.quarantine_max_hosts_per_session
            ):
                return [FileResult(f.url, CAP_HOSTS) for f in files]
            session.execute(
                pg_insert(models.HostDecision)
                .values(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    host=host,
                    state=HostDecisionState.UNDECIDED.value,
                    first_seen_at=now,
                    expires_at=expires,
                )
                .on_conflict_do_nothing(constraint="uq_host_decision_session_host")
            )
            decision = _lock_decision_for_share(session, tenant_id, session_id, host)
        state = decision.state if decision is not None else HostDecisionState.UNDECIDED.value
        if state == HostDecisionState.IGNORED.value:
            return [FileResult(f.url, IGNORED) for f in files]
        if state == HostDecisionState.ADDED.value:
            return [FileResult(f.url, IN_SCOPE) for f in files]
        if state == HostDecisionState.EXPIRED.value:
            # New content from an expired host starts a fresh undecided cycle (compare-and-set).
            session.execute(
                update(models.HostDecision)
                .where(
                    models.HostDecision.session_id == session_id,
                    models.HostDecision.host == host,
                    models.HostDecision.state == HostDecisionState.EXPIRED.value,
                )
                .values(
                    state=HostDecisionState.UNDECIDED.value,
                    first_seen_at=now,
                    decided_at=None,
                    expires_at=expires,
                )
            )

        host_files, host_bytes = _host_usage(session, session_id, host)
        session_bytes = _session_bytes(session, session_id)
        for f in files:
            size = len(f.data)
            if size == 0 or size > settings.max_upload_bytes:
                results.append(FileResult(f.url, INVALID))
                continue
            digest = hashlib.sha256(f.data).hexdigest()
            if session.scalar(
                select(models.QuarantinedAsset.id).where(
                    models.QuarantinedAsset.run_id == run_id,
                    models.QuarantinedAsset.url == f.url,
                    models.QuarantinedAsset.sha256 == digest,
                )
            ):
                results.append(FileResult(f.url, DUPLICATE))
                continue
            if host_files >= settings.quarantine_max_files_per_host:
                results.append(FileResult(f.url, CAP_HOST_FILES))
                continue
            if host_bytes + size > settings.quarantine_max_bytes_per_host:
                results.append(FileResult(f.url, CAP_HOST_BYTES))
                continue
            if session_bytes + size > settings.quarantine_max_bytes_per_session:
                results.append(FileResult(f.url, CAP_SESSION_BYTES))
                continue
            key = storage.put_blob(tenant_id, run_id, "quarantine", f.data)
            session.add(
                models.QuarantinedAsset(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    host=host,
                    url=f.url,
                    sha256=digest,
                    size_bytes=size,
                    content_ref=key,
                )
            )
            host_files += 1
            host_bytes += size
            session_bytes += size
            results.append(FileResult(f.url, STORED))
    return results


def decide(*, tenant_id: str, session_id: str, host: str, decision: str) -> DecisionResult:
    """The researcher's ``"add"`` or ``"ignore"`` for one undecided host."""
    host = normalize_host(host)
    if decision not in ("add", "ignore"):
        raise QuarantineError(f"unknown decision: {decision!r}")
    target = HostDecisionState.ADDED if decision == "add" else HostDecisionState.IGNORED
    now = _now()
    keys_to_delete: list[str] = []
    result = DecisionResult(status="ok", state=target.value)
    with tenant_session(tenant_id) as session:
        won = session.execute(
            update(models.HostDecision)
            .where(
                models.HostDecision.session_id == session_id,
                models.HostDecision.host == host,
                models.HostDecision.state == HostDecisionState.UNDECIDED.value,
            )
            .values(state=target.value, decided_at=now, expires_at=None)
            .returning(models.HostDecision.id)
        ).first()
        if won is None:
            current = session.scalar(
                select(models.HostDecision.state).where(
                    models.HostDecision.session_id == session_id,
                    models.HostDecision.host == host,
                )
            )
            if current is None:
                return DecisionResult(status="not_found")
            # The same decision again is an idempotent success; any other state lost the race.
            return DecisionResult(
                status="ok" if current == target.value else "conflict", state=current
            )
        if target is HostDecisionState.ADDED:
            _add_to_scope(session, session_id, host)
            promoted, keys_to_delete = _promote(session, tenant_id, session_id, host, result)
            result.promoted = promoted
        else:
            result.deleted, keys_to_delete = _delete_content(session, tenant_id, session_id, host)
    result.deleted = result.deleted or len(keys_to_delete)
    _delete_after_commit(keys_to_delete)
    return result


def sweep(*, now: dt.datetime | None = None, limit: int = 200) -> int:
    """Expire hosts undecided past their expiry and delete their content. Returns how many
    hosts expired. Cross-tenant discovery uses the admin session; every change goes through the
    host's own tenant session and the same compare-and-set as a researcher's decision."""
    now = now or _now()
    with admin_session() as session:
        due = session.execute(
            select(
                models.HostDecision.tenant_id,
                models.HostDecision.session_id,
                models.HostDecision.host,
            )
            .where(
                models.HostDecision.state == HostDecisionState.UNDECIDED.value,
                models.HostDecision.expires_at < now,
            )
            .limit(limit)
        ).all()
    expired = 0
    for tenant_id, session_id, host in due:
        try:
            expired += _expire(str(tenant_id), str(session_id), host, now)
        except Exception:  # noqa: BLE001 - one broken host must not stop the sweep
            log.exception("quarantine.expire_failed", session_id=str(session_id), host=host)
    return expired


def undecided_hosts(*, tenant_id: str, session_id: str) -> list[dict]:
    """The session's undecided hosts with what is held for each (for the popup and Hosts page)."""
    with tenant_session(tenant_id) as session:
        rows = session.execute(
            select(
                models.HostDecision.host,
                models.HostDecision.first_seen_at,
                models.HostDecision.expires_at,
                func.count(models.QuarantinedAsset.id),
                func.coalesce(func.sum(models.QuarantinedAsset.size_bytes), 0),
            )
            .outerjoin(
                models.QuarantinedAsset,
                (models.QuarantinedAsset.session_id == models.HostDecision.session_id)
                & (models.QuarantinedAsset.host == models.HostDecision.host),
            )
            .where(
                models.HostDecision.session_id == session_id,
                models.HostDecision.state == HostDecisionState.UNDECIDED.value,
            )
            .group_by(models.HostDecision.id)
            .order_by(models.HostDecision.first_seen_at)
        ).all()
    return [
        {
            "host": host,
            "first_seen_at": first_seen.isoformat(),
            "expires_at": expires.isoformat() if expires else None,
            "files": int(files),
            "bytes": int(size),
        }
        for host, first_seen, expires, files, size in rows
    ]


def ignored_hosts(*, tenant_id: str, session_id: str) -> list[str]:
    """The session's ignore list (hosts whose future scripts are never stored)."""
    with tenant_session(tenant_id) as session:
        return sorted(
            session.scalars(
                select(models.HostDecision.host).where(
                    models.HostDecision.session_id == session_id,
                    models.HostDecision.state == HostDecisionState.IGNORED.value,
                )
            )
        )


# --------------------------------------------------------------------------- internals ---- #


def _lock_decision_for_share(session, tenant_id: str, session_id: str, host: str):
    return session.scalar(
        select(models.HostDecision)
        .where(models.HostDecision.session_id == session_id, models.HostDecision.host == host)
        .with_for_update(read=True)
    )


def _undecided_host_count(session, session_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(models.HostDecision)
            .where(
                models.HostDecision.session_id == session_id,
                models.HostDecision.state == HostDecisionState.UNDECIDED.value,
            )
        )
        or 0
    )


def _host_usage(session, session_id: str, host: str) -> tuple[int, int]:
    count, size = session.execute(
        select(func.count(), func.coalesce(func.sum(models.QuarantinedAsset.size_bytes), 0)).where(
            models.QuarantinedAsset.session_id == session_id, models.QuarantinedAsset.host == host
        )
    ).one()
    return int(count), int(size)


def _session_bytes(session, session_id: str) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(models.QuarantinedAsset.size_bytes), 0)).where(
                models.QuarantinedAsset.session_id == session_id
            )
        )
        or 0
    )


def _add_to_scope(session, session_id: str, host: str) -> None:
    scope = session.scalar(
        select(models.EngagementSession.scope_hosts).where(
            models.EngagementSession.id == session_id
        )
    )
    scope = list(scope or [])
    if host not in scope:
        scope.append(host)
        session.execute(
            update(models.EngagementSession)
            .where(models.EngagementSession.id == session_id)
            .values(scope_hosts=scope)
        )


def _promote(
    session, tenant_id: str, session_id: str, host: str, result: DecisionResult
) -> tuple[int, list[str]]:
    """Insert the host's quarantined scripts as normal run assets (each into its own run), then
    delete the quarantine rows. Returns ``(promoted count, quarantine blob keys now unreferenced)``."""
    settings = get_settings()
    rows = session.scalars(
        select(models.QuarantinedAsset)
        .where(
            models.QuarantinedAsset.session_id == session_id, models.QuarantinedAsset.host == host
        )
        .order_by(models.QuarantinedAsset.created_at)
    ).all()
    by_run: dict[str, list[models.QuarantinedAsset]] = {}
    for row in rows:
        by_run.setdefault(str(row.run_id), []).append(row)
    promoted = 0
    for run_id, assets in by_run.items():
        captured = []
        for asset in assets:
            data = storage.get_blob(asset.content_ref)
            key = storage.put_blob(tenant_id, run_id, "input", data)
            captured.append(captured_versions.CapturedAsset(url=asset.url, input_ref=key))
        added = captured_versions.add_captured_versions(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            files=captured,
            max_versions=settings.capture_max_versions_per_url,
        )
        promoted += len(captured) - len(added.capped) - len(added.vanished)
        state = session.scalar(select(models.Run.state).where(models.Run.id == run_id))
        if state is not None and RunState(state) not in TERMINAL_STATES:
            capture_lifecycle.note_batch(session, run_id)  # schedule the next analysis pass
            result.analysis[run_id] = "scheduled"
        else:
            result.analysis[run_id] = "needs_rerun"
    keys = _delete_rows_then_keys(session, tenant_id, rows)
    return promoted, keys


def _delete_content(session, tenant_id: str, session_id: str, host: str) -> tuple[int, list[str]]:
    rows = session.scalars(
        select(models.QuarantinedAsset).where(
            models.QuarantinedAsset.session_id == session_id, models.QuarantinedAsset.host == host
        )
    ).all()
    keys = _delete_rows_then_keys(session, tenant_id, rows)
    return len(rows), keys


def _delete_rows_then_keys(session, tenant_id: str, rows: list) -> list[str]:
    """Delete the quarantine rows, then name the blobs no remaining row of their run uses."""
    if not rows:
        return []
    session.execute(
        delete(models.QuarantinedAsset).where(models.QuarantinedAsset.id.in_([r.id for r in rows]))
    )
    keys: list[str] = []
    for run_id in {str(r.run_id) for r in rows}:
        refs = [r.content_ref for r in rows if str(r.run_id) == run_id]
        keys += blobs.unreferenced_keys(
            session, tenant_id=tenant_id, run_id=run_id, quarantine_refs=refs
        )
    return keys


def _delete_after_commit(keys: list[str]) -> None:
    try:
        blobs.delete_keys(keys)
    except Exception:  # noqa: BLE001 - the rows are gone; a leftover blob is purged with the run
        log.warning("quarantine.blob_delete_failed", keys=len(keys))


def _expire(tenant_id: str, session_id: str, host: str, now: dt.datetime) -> int:
    keys: list[str] = []
    with tenant_session(tenant_id) as session:
        won = session.execute(
            update(models.HostDecision)
            .where(
                models.HostDecision.session_id == session_id,
                models.HostDecision.host == host,
                models.HostDecision.state == HostDecisionState.UNDECIDED.value,
                models.HostDecision.expires_at < now,
            )
            .values(state=HostDecisionState.EXPIRED.value, decided_at=now, expires_at=None)
            .returning(models.HostDecision.id)
        ).first()
        if won is None:
            return 0  # decided meanwhile: the researcher's choice stands
        _count, keys = _delete_content(session, tenant_id, session_id, host)
    _delete_after_commit(keys)
    return 1
