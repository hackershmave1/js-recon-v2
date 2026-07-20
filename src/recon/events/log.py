"""Durable event log (REQ-R2) and the commit-then-publish relay.

Events are written to the append-only ``run_event`` table inside the same
transaction as the state change that produced them (so they can never be lost or
double-counted relative to that change), then published to the Redis fast-path
after the transaction commits.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from redis import Redis
from sqlalchemy.orm import Session

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import RunEvent


class RecordedEvent(NamedTuple):
    pg_id: int
    run_id: str
    event_type: str
    payload: dict[str, Any]


def record_event(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> RecordedEvent:
    """Append one event within the caller's open transaction (durable mirror).

    Flushes so the row's ``id`` is assigned; the caller publishes to Redis after
    the surrounding transaction commits.
    """
    row = RunEvent(
        tenant_id=tenant_id, run_id=run_id, type=event_type, payload=payload
    )
    session.add(row)
    session.flush()
    return RecordedEvent(
        pg_id=row.id, run_id=str(run_id), event_type=event_type, payload=payload
    )


def publish(redis: Redis, event: RecordedEvent) -> str:
    """Publish a recorded event to its run's Redis stream (fast-path)."""
    from recon.events import stream

    return stream.publish(
        redis,
        event.run_id,
        pg_id=event.pg_id,
        event_type=event.event_type,
        payload=event.payload,
        maxlen=get_settings().event_stream_maxlen,
    )


def emit(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> RecordedEvent:
    """Record + publish a standalone event (not tied to a state change).

    Used for progress/heartbeat events. Opens its own short transaction.
    """
    with tenant_session(tenant_id) as session:
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
        )
    publish(redis, event)
    return event
