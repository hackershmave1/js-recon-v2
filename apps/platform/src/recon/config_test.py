"""Unit tests for config helpers — clamp_fetch_bytes (per-run fetch-cap, REQ-Q5) +
the D32-A1 source-map cap default."""

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


def test_source_map_cap_default_is_larger_than_bundle_cap() -> None:
    # D32-A1: the .map fetch gets its OWN declared-default cap, larger than the default
    # bundle cap (10 MiB) — a real source map is 3-6x its bundle, so sharing a cap would
    # soft-drop the map. D37-L1: raised to 96 MiB so a real >32 MiB enterprise-bundle map
    # is recovered, not skipped (safe only because D37-L0 now memory-bounds recovery).
    # Assert the DECLARED defaults — env-independent (a stray RECON_* / .env can't flip it).
    fields = Settings.model_fields
    assert fields["max_source_map_bytes"].default == 96 * 1024 * 1024
    assert fields["max_source_map_bytes"].default > fields["max_fetch_bytes"].default


def test_sourcemapper_memory_limit_default_clears_the_measured_go_floor() -> None:
    # D37-L0: the recovery child's RLIMIT_AS ceiling. A 32-96 MiB map was MEASURED to need
    # ~2 GiB of virtual under the pinned Go binary (Go over-reserves virtual; a <=1.5 GiB
    # ceiling regresses even a 32 MiB map that recovers today). The default must clear that
    # floor with headroom, AND stay above the (recovery-time) map cap it protects.
    fields = Settings.model_fields
    limit = fields["sourcemapper_memory_limit_bytes"].default
    assert limit == 3 * 1024 * 1024 * 1024
    assert limit > 2 * 1024 * 1024 * 1024  # the measured recover-vs-trip floor
    assert limit > fields["max_source_map_bytes"].default
