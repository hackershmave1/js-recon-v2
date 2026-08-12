"""Unit tests for config helpers — clamp_fetch_bytes (per-run fetch-cap, REQ-Q5)."""

from __future__ import annotations

from recon.config import Settings, clamp_fetch_bytes


def _settings(*, default: int, ceiling: int) -> Settings:
    return Settings(max_fetch_bytes=default, max_fetch_bytes_ceiling=ceiling)


def test_none_override_uses_global_default() -> None:
    s = _settings(default=10, ceiling=32)
    assert clamp_fetch_bytes(None, s) == 10


def test_positive_override_below_ceiling_is_used() -> None:
    s = _settings(default=10, ceiling=32)
    assert clamp_fetch_bytes(20, s) == 20


def test_override_above_ceiling_is_capped_to_ceiling() -> None:
    s = _settings(default=10, ceiling=32)
    assert clamp_fetch_bytes(64, s) == 32


def test_zero_override_falls_back_to_global() -> None:
    s = _settings(default=10, ceiling=32)
    assert clamp_fetch_bytes(0, s) == 10


def test_negative_override_fails_closed_to_global_not_unbounded() -> None:
    # A negative int is truthy in Python: `run_cap or default` would leak -1 straight
    # through as max_bytes, and `len(body) > -1` fires on the first chunk — an
    # effectively-unbounded fetch. Must fail closed to the global default (REQ-Q5).
    s = _settings(default=10, ceiling=32)
    assert clamp_fetch_bytes(-1, s) == 10


def test_ceiling_bounds_even_the_global_default() -> None:
    # If ops sets the global above the ceiling, the ceiling is still the hard bound.
    s = _settings(default=100, ceiling=32)
    assert clamp_fetch_bytes(None, s) == 32
    assert clamp_fetch_bytes(50, s) == 32
