"""Per-run Redis event stream — the hot path for SSE (REQ-R2).

Each run has its own capped stream ``run:{run_id}:events``. Redis assigns each
entry a monotonic id which doubles as the SSE ``Last-Event-ID``; on reconnect we
replay everything after that id, so a brief disconnect never loses events. The
durable copy lives in the ``run_event`` table (see :mod:`recon.events.log`).
"""

from __future__ import annotations

import json
from typing import Any

from redis import Redis


def stream_key(run_id: str) -> str:
    return f"run:{run_id}:events"


def publish(
    redis: Redis,
    run_id: str,
    *,
    pg_id: int,
    event_type: str,
    payload: dict[str, Any],
    maxlen: int,
) -> str:
    """XADD one event to the run's stream (capped). Returns the entry id."""
    fields = {
        "type": event_type,
        "pg_id": str(pg_id),
        "payload": json.dumps(payload, separators=(",", ":")),
    }
    entry_id = redis.xadd(stream_key(run_id), fields, maxlen=maxlen, approximate=True)
    return entry_id.decode() if isinstance(entry_id, bytes) else entry_id


def _decode(entry: tuple[Any, dict[Any, Any]]) -> dict[str, Any]:
    entry_id, raw = entry
    fields = {
        (k.decode() if isinstance(k, bytes) else k): (
            v.decode() if isinstance(v, bytes) else v
        )
        for k, v in raw.items()
    }
    return {
        "id": entry_id.decode() if isinstance(entry_id, bytes) else entry_id,
        "type": fields.get("type"),
        "pg_id": int(fields["pg_id"]) if fields.get("pg_id") else None,
        "payload": json.loads(fields["payload"]) if fields.get("payload") else {},
    }


def replay(redis: Redis, run_id: str, last_id: str | None) -> list[dict[str, Any]]:
    """Every event after ``last_id`` (exclusive). ``None`` replays from the start."""
    start = f"({last_id}" if last_id else "-"
    entries = redis.xrange(stream_key(run_id), min=start, max="+")
    return [_decode(e) for e in entries]


def tail(
    redis: Redis, run_id: str, last_id: str, *, block_ms: int, count: int = 50
) -> list[dict[str, Any]]:
    """Block up to ``block_ms`` for events after ``last_id``. Empty on timeout."""
    result = redis.xread({stream_key(run_id): last_id}, count=count, block=block_ms)
    if not result:
        return []
    _key, entries = result[0]
    return [_decode(e) for e in entries]
