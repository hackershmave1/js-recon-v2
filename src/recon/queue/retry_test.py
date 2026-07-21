"""Colocated tests for the retry policy (REQ-Q2)."""

from __future__ import annotations

import random

import pytest

from recon.queue import retry


@pytest.mark.parametrize(
    "status,expected",
    [(429, True), (500, True), (503, True), (400, False), (401, False), (404, False)],
)
def test_http_retryable_classification(status, expected):
    assert retry.http_retryable(status) is expected


def test_fatal_error_is_not_retryable():
    assert retry.is_retryable(retry.FatalError("bad input")) is False


def test_retryable_error_is_retryable():
    assert retry.is_retryable(retry.RetryableError("timeout")) is True


def test_unknown_error_defaults_to_retryable():
    assert retry.is_retryable(RuntimeError("???")) is True


def test_backoff_is_bounded_by_ceiling():
    rng = random.Random(1234)
    for attempt in range(1, 8):
        delay = retry.compute_delay(
            attempt, base_delay=1.0, max_delay=60.0, rng=rng
        )
        ceiling = min(60.0, 1.0 * (2 ** (attempt - 1)))
        assert 0.0 <= delay <= ceiling


def test_backoff_ceiling_caps_at_max_delay():
    rng = random.Random(0)
    # Attempt 10 would be 512s uncapped; must never exceed the 60s cap.
    for _ in range(100):
        assert retry.compute_delay(10, base_delay=1.0, max_delay=60.0, rng=rng) <= 60.0


def test_compute_delay_rejects_zero_attempt():
    with pytest.raises(ValueError):
        retry.compute_delay(0, base_delay=1.0, max_delay=60.0)


def test_should_retry_respects_attempt_cap():
    assert retry.should_retry(1, 3, retry.RetryableError()) is True
    assert retry.should_retry(3, 3, retry.RetryableError()) is False
    assert retry.should_retry(1, 3, retry.FatalError()) is False


def test_retryable_error_carries_optional_retry_after():
    assert retry.RetryableError("x").retry_after is None
    assert retry.RetryableError("x", retry_after=5.0).retry_after == 5.0
    # Still retryable regardless of the hint.
    assert retry.is_retryable(retry.RetryableError("x", retry_after=5.0)) is True
