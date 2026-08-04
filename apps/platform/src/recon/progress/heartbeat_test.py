"""Colocated tests for the pure stalled-vs-running decision (REQ-R3)."""

from __future__ import annotations

import datetime as dt

from recon.progress.heartbeat import is_stalled


def _t(seconds_ago: float, now: dt.datetime) -> dt.datetime:
    return now - dt.timedelta(seconds=seconds_ago)


def test_recent_heartbeat_is_not_stalled():
    now = dt.datetime.now(dt.timezone.utc)
    assert is_stalled(active=True, heartbeat_at=_t(5, now), now=now, threshold_s=30) is False


def test_old_heartbeat_on_active_run_is_stalled():
    now = dt.datetime.now(dt.timezone.utc)
    assert is_stalled(active=True, heartbeat_at=_t(45, now), now=now, threshold_s=30) is True


def test_inactive_run_is_never_stalled():
    now = dt.datetime.now(dt.timezone.utc)
    assert is_stalled(active=False, heartbeat_at=_t(999, now), now=now, threshold_s=30) is False


def test_missing_heartbeat_is_not_stalled():
    now = dt.datetime.now(dt.timezone.utc)
    assert is_stalled(active=True, heartbeat_at=None, now=now, threshold_s=30) is False
