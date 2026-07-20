"""Redis Streams broker (REQ-Q1, REQ-Q2).

One stream per work class, one consumer group ("workers") per stream. Delivery
is at-least-once: a message stays in the group's pending list until it is
acknowledged. Failures are re-scheduled with backoff via a per-queue ZSET (Redis
Streams have no native delayed delivery) and promoted back when due; exhausted
messages go to a per-queue dead-letter stream. Messages abandoned by a crashed
worker are reclaimed with XAUTOCLAIM.
"""

from __future__ import annotations

import json
import time
from typing import Any

from redis import Redis
from redis.exceptions import ResponseError

from recon.domain import QueueName

GROUP = "workers"


def queue_key(queue: QueueName) -> str:
    return f"queue:{queue.value}"


def dlq_key(queue: QueueName) -> str:
    return f"queue:{queue.value}:dlq"


def scheduled_key(queue: QueueName) -> str:
    return f"queue:{queue.value}:scheduled"


def ensure_group(redis: Redis, queue: QueueName) -> None:
    """Create the stream + consumer group if absent (idempotent)."""
    try:
        redis.xgroup_create(queue_key(queue), GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _encode(message: dict[str, Any]) -> dict[str, str]:
    return {"data": json.dumps(message, separators=(",", ":"))}


def _decode(raw: dict[Any, Any]) -> dict[str, Any]:
    fields = {
        (k.decode() if isinstance(k, bytes) else k): (
            v.decode() if isinstance(v, bytes) else v
        )
        for k, v in raw.items()
    }
    return json.loads(fields["data"])


def _as_str(msg_id: Any) -> str:
    return msg_id.decode() if isinstance(msg_id, bytes) else msg_id


def enqueue(redis: Redis, queue: QueueName, message: dict[str, Any]) -> str:
    """Append a job to its queue stream. Returns the entry id."""
    return _as_str(redis.xadd(queue_key(queue), _encode(message)))


def read_batch(
    redis: Redis,
    queue: QueueName,
    consumer: str,
    *,
    count: int = 10,
    block_ms: int = 2000,
) -> list[tuple[str, dict[str, Any]]]:
    """Claim up to ``count`` new messages for this consumer (adds to the PEL)."""
    result = redis.xreadgroup(
        GROUP, consumer, {queue_key(queue): ">"}, count=count, block=block_ms
    )
    if not result:
        return []
    _key, entries = result[0]
    return [(_as_str(mid), _decode(raw)) for mid, raw in entries]


def ack(redis: Redis, queue: QueueName, msg_id: str) -> None:
    redis.xack(queue_key(queue), GROUP, msg_id)


def reschedule(
    redis: Redis, queue: QueueName, message: dict[str, Any], delay_seconds: float
) -> None:
    """Hold a job in the scheduled set until ``now + delay`` (backoff)."""
    due = time.time() + max(0.0, delay_seconds)
    redis.zadd(scheduled_key(queue), {json.dumps(message, separators=(",", ":")): due})


def promote_due(redis: Redis, queue: QueueName, now: float | None = None) -> int:
    """Move every due scheduled job back onto the stream. Returns how many."""
    cutoff = now if now is not None else time.time()
    key = scheduled_key(queue)
    due = redis.zrangebyscore(key, min=0, max=cutoff)
    promoted = 0
    for member in due:
        text = member.decode() if isinstance(member, bytes) else member
        # Remove first so a concurrent promoter can't double-enqueue it.
        if redis.zrem(key, member):
            redis.xadd(queue_key(queue), _encode(json.loads(text)))
            promoted += 1
    return promoted


def to_dlq(
    redis: Redis, queue: QueueName, message: dict[str, Any], error: str
) -> str:
    """Route a poison message to the per-queue dead-letter stream with its cause."""
    fields = _encode(message)
    fields["error"] = error
    return _as_str(redis.xadd(dlq_key(queue), fields))


def reclaim_stalled(
    redis: Redis,
    queue: QueueName,
    consumer: str,
    *,
    min_idle_ms: int,
    count: int = 10,
) -> list[tuple[str, dict[str, Any]]]:
    """Reclaim messages abandoned by a crashed worker (idle > threshold)."""
    result = redis.xautoclaim(
        queue_key(queue), GROUP, consumer, min_idle_ms, start_id="0-0", count=count
    )
    # redis-py returns [next_cursor, claimed, deleted]; older/fake returns 2 items.
    claimed = result[1] if len(result) >= 2 else []
    out: list[tuple[str, dict[str, Any]]] = []
    for mid, raw in claimed:
        if raw:  # deleted entries come back with empty fields
            out.append((_as_str(mid), _decode(raw)))
    return out


def pending_count(redis: Redis, queue: QueueName) -> int:
    summary = redis.xpending(queue_key(queue), GROUP)
    if isinstance(summary, dict):
        return int(summary.get("pending", 0))
    return int(summary[0]) if summary else 0


def _min_pending_id(redis: Redis, queue: QueueName) -> str | None:
    summary = redis.xpending(queue_key(queue), GROUP)
    if isinstance(summary, dict):
        count, low = summary.get("pending", 0), summary.get("min")
    else:
        count, low = (summary[0], summary[1]) if summary else (0, None)
    if not count or low is None:
        return None
    return low.decode() if isinstance(low, bytes) else low


def trim_acked(redis: Redis, queue: QueueName, *, idle_keep: int = 1000) -> None:
    """Bound queue-stream growth. XACK does not delete entries, so without this a
    queue stream grows forever. Trimming below the lowest still-pending id can
    never drop un-acked work; when nothing is pending we cap to a recent window.
    """
    key = queue_key(queue)
    low = _min_pending_id(redis, queue)
    if low is not None:
        redis.xtrim(key, minid=low)
    else:
        redis.xtrim(key, maxlen=idle_keep, approximate=True)
