"""Fetch politeness / rate limiting (REQ-Q3).

A single target must never be hammered, and total outbound fetch pressure is
bounded by a global budget. Both limits live in Redis so they hold across every
worker in the cluster, not just within one process.

Two independent gates, checked in order:

1. **Per-host min-interval** — at most one fetch to a given host per
   ``fetch_min_host_interval_seconds``. Implemented as ``SET key NX PX interval``:
   the first caller wins the slot; the key's TTL is the enforced gap, so a
   concurrent second caller (any worker) is told to wait out the remaining TTL.
   This is the "never hammer one target" guarantee.
2. **Global budget** — at most ``fetch_global_max_per_second`` fetches per wall
   second across all hosts, via a per-second ``INCR`` counter. Protects the
   platform/network as a whole even when many distinct hosts are in play.

``check`` never blocks. It returns ``0.0`` when the fetch may proceed, or the
seconds to wait otherwise; the caller reschedules the job with that backoff
(``recon.fetch.fetch.fetch_run`` -> ``retry.RetryableError(retry_after=...)``).

robots.txt handling is deliberately out of scope here: it is only meaningful once
crawling discovers multiple paths on a host, so it rides with the (deferred)
DISCOVER stage. For a single user-supplied, in-scope, authorized URL there is no
path set to consult.
"""

from __future__ import annotations

import time

from redis import Redis

from recon.config import Settings, get_settings

_HOST_KEY = "ratelimit:fetch:host:{host}"
_GLOBAL_KEY = "ratelimit:fetch:global:{second}"


def check(
    redis: Redis,
    host: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> float:
    """Seconds the caller must wait before fetching ``host`` (``0.0`` = go now).

    Checks the per-host min-interval first (the primary anti-hammer gate) so a
    throttled host never even consumes a global-budget slot."""
    settings = settings or get_settings()
    now = time.time() if now is None else now

    host_wait = _check_host_interval(redis, host, settings)
    if host_wait > 0:
        return host_wait
    return _check_global_budget(redis, now, settings)


def _check_host_interval(redis: Redis, host: str, settings: Settings) -> float:
    interval = settings.fetch_min_host_interval_seconds
    if interval <= 0:
        return 0.0
    key = _HOST_KEY.format(host=host.lower())
    interval_ms = int(interval * 1000)
    # NX: only the first caller in this interval sets it and proceeds. PX: the key
    # self-expires after the interval, so the next fetch is allowed automatically.
    won = redis.set(key, "1", nx=True, px=interval_ms)
    if won:
        return 0.0
    ttl_ms = redis.pttl(key)
    # PTTL is -1 (no expiry) / -2 (just expired) in the race window — retry shortly.
    return interval if ttl_ms is None or ttl_ms < 0 else ttl_ms / 1000.0


def _check_global_budget(redis: Redis, now: float, settings: Settings) -> float:
    budget = settings.fetch_global_max_per_second
    if budget <= 0:
        return 0.0
    second = int(now)
    key = _GLOBAL_KEY.format(second=second)
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, 2)  # outlive the 1s window so the counter can't leak
    if count <= budget:
        return 0.0
    # Over budget for this second — wait until the next one starts.
    wait = 1.0 - (now - second)
    return wait if wait > 0 else 1.0
