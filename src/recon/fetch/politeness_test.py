"""Unit tests for the fetch politeness limiter (REQ-Q3).

Uses fakeredis so no live Redis is needed; ``now`` is injected for deterministic
global-window boundaries. The per-host gate is exercised by hitting the same key
twice in one interval (the second is throttled) — no time travel required.
"""

from __future__ import annotations

import fakeredis

from recon.config import Settings
from recon.fetch import politeness


def _settings(**overrides) -> Settings:
    base = {"fetch_min_host_interval_seconds": 5.0, "fetch_global_max_per_second": 1000}
    base.update(overrides)
    return Settings(**base)


def test_first_hit_to_a_host_is_allowed_then_throttled():
    redis = fakeredis.FakeRedis()
    settings = _settings()
    assert politeness.check(redis, "acme.io", settings=settings) == 0.0
    # A second immediate hit to the SAME host must wait out the interval.
    wait = politeness.check(redis, "acme.io", settings=settings)
    assert 0 < wait <= 5.0


def test_distinct_hosts_are_independent():
    redis = fakeredis.FakeRedis()
    settings = _settings()
    assert politeness.check(redis, "acme.io", settings=settings) == 0.0
    assert politeness.check(redis, "other.io", settings=settings) == 0.0


def test_host_gate_is_case_insensitive():
    redis = fakeredis.FakeRedis()
    settings = _settings()
    assert politeness.check(redis, "ACME.io", settings=settings) == 0.0
    # Same host, different case — must collapse to the same slot and throttle.
    assert politeness.check(redis, "acme.io", settings=settings) > 0


def test_global_budget_caps_per_second():
    redis = fakeredis.FakeRedis()
    # Disable the host gate so the global budget is isolated; distinct hosts.
    settings = _settings(fetch_min_host_interval_seconds=0.0, fetch_global_max_per_second=3)
    waits = [politeness.check(redis, f"h{i}.io", now=1000.0, settings=settings) for i in range(4)]
    assert waits[:3] == [0.0, 0.0, 0.0]
    assert waits[3] > 0  # the 4th in the same second exceeds the budget


def test_global_budget_resets_next_second():
    redis = fakeredis.FakeRedis()
    settings = _settings(fetch_min_host_interval_seconds=0.0, fetch_global_max_per_second=1)
    assert politeness.check(redis, "h.io", now=1000.0, settings=settings) == 0.0
    assert politeness.check(redis, "h.io", now=1000.4, settings=settings) > 0  # same second
    assert politeness.check(redis, "h.io", now=1001.0, settings=settings) == 0.0  # next second


def test_zero_config_disables_gates():
    redis = fakeredis.FakeRedis()
    settings = _settings(fetch_min_host_interval_seconds=0.0, fetch_global_max_per_second=0)
    # Both gates off -> always allowed (escape hatch, never negative).
    for _ in range(5):
        assert politeness.check(redis, "acme.io", now=1000.0, settings=settings) == 0.0
