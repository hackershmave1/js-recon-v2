"""One analysis pass over a capture: the pieces ``capture_lifecycle`` (the extension's calls) and
``capture_sweep`` (the worker's tick) share.

A pass is the ordinary pipeline (DISCOVERING → … → CORRELATING), started by a fresh
``discover.assets`` manifest that lists every captured file and carries every runtime observation
so far. It ends resting in CAPTURING, or finished (``coordinator._finalize_state``). Only one pass
may be queued or running per run: ``can_start_pass`` checks for a live Job under the run-row lock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from redis import Redis
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from recon import storage
from recon.config import get_settings
from recon.db.models import Job, Run
from recon.discover import manifest as discovery
from recon.domain import AssetStatus, DiscoveryStatus, JobState, RunStage, RunState
from recon.events.log import RecordedEvent, publish
from recon.runs import assets, coordinator

LIVE_JOB_STATES = (JobState.QUEUED.value, JobState.RUNNING.value)

Pending = tuple[RecordedEvent, dict | None]  # (discover.assets event, job message) to publish


@dataclass(frozen=True)
class Prepared:
    """A capture's current manifest, written to S3 and not yet recorded."""

    found: discovery.Discovery
    unanalyzed: bool  # a captured file no pass has analyzed yet
    new_observations: bool  # this call added observations the last manifest didn't have


def locked(session: Session, run_id: str) -> Run | None:
    """The run row, locked for this transaction: one caller decides at a time."""
    return session.scalar(select(Run).where(Run.id == str(run_id)).with_for_update())


def has_job(session: Session, run_id: str) -> bool:
    return bool(session.scalar(select(exists(select(Job.id).where(Job.run_id == str(run_id))))))


def live_job_exists(session: Session, run_id: str) -> bool:
    return bool(
        session.scalar(
            select(
                exists(
                    select(Job.id).where(Job.run_id == str(run_id), Job.state.in_(LIVE_JOB_STATES))
                )
            )
        )
    )


def can_start_pass(session: Session, run: Run) -> bool:
    """No pass is queued or running: the run rests, or was never analyzed. A queued pass leaves the
    run in CAPTURING until the worker picks it up, so the state alone can't tell."""
    state = RunState(run.state)
    if state not in (RunState.CAPTURING, RunState.QUEUED):
        return False
    return not live_job_exists(session, str(run.id))


def begin_pass(session: Session, tenant_id: str, run: Run, found: discovery.Discovery) -> Pending:
    """In the caller's transaction: the pass's manifest event and its DISCOVERING Job. The worker
    moves the run QUEUED/CAPTURING → DISCOVERING when it picks the job up."""
    event = discovery.record(session, tenant_id=tenant_id, run_id=str(run.id), found=found)
    run.reanalyze_after = None
    _, message = coordinator.create_stage_job(
        session, tenant_id=tenant_id, run_id=str(run.id), stage=RunStage.DISCOVERING
    )
    return event, message


def publish_pending(redis: Redis, pending: Pending | None) -> None:
    """After commit: a subscriber must never see an unpersisted event or job."""
    if pending is None:
        return
    event, message = pending
    publish(redis, event)
    if message is not None:
        coordinator.publish_stage_job(redis, message)


def prepare(tenant_id: str, run_id: str, observations: list[dict]) -> Prepared | None:
    """Write the run's current manifest (every captured file, every observation so far) to S3.
    ``None`` when the run has no files. Called BEFORE the run-row lock, so the lock never spans an
    S3 round trip. The blobs are content-addressed, so a caller that then loses the race wrote
    nothing harmful."""
    rows = assets.list_for_run(tenant_id, run_id)
    if not rows:
        return None
    merged, added = merged_observations(discovery.read_latest(tenant_id, run_id), observations)
    found = discovery.store_manifest(
        tenant_id=tenant_id,
        run_id=run_id,
        domain=next((h for h in (urlsplit(r.url).hostname for r in rows) if h), run_id),
        status=DiscoveryStatus.OK,
        asset_urls=[r.url for r in rows],
        source="extension",
        observed_requests=merged or None,
    )
    unanalyzed = any(
        r.fetch_status == AssetStatus.OK.value and r.analyze_status == AssetStatus.PENDING.value
        for r in rows
    )
    return Prepared(found=found, unanalyzed=unanalyzed, new_observations=added > 0)


def merged_observations(
    previous: discovery.Discovery | None, new: list[dict]
) -> tuple[list[dict], int]:
    """Earlier observations first, then new ones not seen before (first sighting wins), capped
    at ``capture_max_requests`` like a single call (D45). Correlate reads only the LATEST
    manifest, so every manifest carries the whole set. Returns ``(merged, how many were added)``."""
    cap = get_settings().capture_max_requests
    merged: list[dict] = []
    if previous is not None and previous.requests_ref:
        merged = list(json.loads(storage.get_blob(previous.requests_ref)))[:cap]
    seen = {(o.get("method"), o.get("url")) for o in merged}
    added = 0
    for item in new:
        if len(merged) >= cap:
            break
        key = (item.get("method"), item.get("url"))
        if key not in seen:
            seen.add(key)
            merged.append(item)
            added += 1
    return merged, added
