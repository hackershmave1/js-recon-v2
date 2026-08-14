"""Unit tests for the login brute-force throttle (fast lane).

Uses fakeredis so no live Redis is needed (mirrors ``fetch/politeness_test``). The
full "N attempts -> 429" *route* path can't run here — attempts below the limit call
``authenticate()`` which needs PG — so the route's 429 short-circuit is covered
hermetically in ``api/auth_router_test`` by pre-seeding the counter; here we test the
limiter logic directly.
"""

from __future__ import annotations

import fakeredis
from redis import RedisError

from recon.auth import login_rate_limit
from recon.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "login_ratelimit_max_attempts": 3,
        "login_ratelimit_window_seconds": 300.0,
        "login_ratelimit_global_max_attempts": 1000,
    }
    base.update(overrides)
    return Settings(**base)


def test_allows_until_per_user_limit_then_blocks():
    redis = fakeredis.FakeRedis()
    settings = _settings(login_ratelimit_max_attempts=3)
    # Each of the first 3 attempts is allowed at check time, then recorded as a failure.
    for _ in range(3):
        assert login_rate_limit.retry_after_seconds(redis, "admin", settings=settings) == 0.0
        login_rate_limit.record_failure(redis, "admin", settings=settings)
    # The 4th check sees count == limit and blocks, with a wait bounded by the window.
    wait = login_rate_limit.retry_after_seconds(redis, "admin", settings=settings)
    assert 0 < wait <= 300.0


def test_clear_resets_the_user_counter_on_success():
    redis = fakeredis.FakeRedis()
    settings = _settings(login_ratelimit_max_attempts=2)
    for _ in range(2):
        login_rate_limit.record_failure(redis, "admin", settings=settings)
    assert login_rate_limit.retry_after_seconds(redis, "admin", settings=settings) > 0
    login_rate_limit.clear(redis, "admin", settings=settings)
    assert login_rate_limit.retry_after_seconds(redis, "admin", settings=settings) == 0.0


def test_user_counter_is_case_insensitive():
    redis = fakeredis.FakeRedis()
    settings = _settings(login_ratelimit_max_attempts=2)
    # "Admin" and "admin" are one identity (normalize_username), so they share a bucket.
    login_rate_limit.record_failure(redis, "Admin", settings=settings)
    login_rate_limit.record_failure(redis, "admin", settings=settings)
    assert login_rate_limit.retry_after_seconds(redis, "ADMIN", settings=settings) > 0


def test_global_backstop_trips_across_distinct_usernames():
    redis = fakeredis.FakeRedis()
    # Per-user cap high (never trips); global cap low; every failure a DIFFERENT user.
    settings = _settings(login_ratelimit_max_attempts=100, login_ratelimit_global_max_attempts=3)
    for i in range(3):
        assert login_rate_limit.retry_after_seconds(redis, f"user{i}", settings=settings) == 0.0
        login_rate_limit.record_failure(redis, f"user{i}", settings=settings)
    # A brand-new username, its own per-user counter empty, is blocked by the GLOBAL gate.
    assert login_rate_limit.retry_after_seconds(redis, "fresh-user", settings=settings) > 0


def test_max_attempts_zero_disables_the_limiter():
    redis = fakeredis.FakeRedis()
    settings = _settings(login_ratelimit_max_attempts=0)
    for _ in range(10):
        login_rate_limit.record_failure(redis, "admin", settings=settings)
        assert login_rate_limit.retry_after_seconds(redis, "admin", settings=settings) == 0.0
    assert redis.keys("ratelimit:login:*") == []  # disabled -> nothing written


class _BrokenRedis:
    """A Redis stand-in whose every op raises, to prove the limiter fails OPEN."""

    def get(self, *args, **kwargs):
        raise RedisError("down")

    def ttl(self, *args, **kwargs):
        raise RedisError("down")

    def pipeline(self, *args, **kwargs):
        raise RedisError("down")

    def delete(self, *args, **kwargs):
        raise RedisError("down")


def test_fails_open_on_redis_error():
    broken = _BrokenRedis()
    settings = _settings()
    # A limiter outage must never block a login, and must never raise.
    assert login_rate_limit.retry_after_seconds(broken, "admin", settings=settings) == 0.0
    login_rate_limit.record_failure(broken, "admin", settings=settings)
    login_rate_limit.clear(broken, "admin", settings=settings)
