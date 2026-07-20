"""Retry classification and backoff (REQ-Q2), pure and deterministic under a seed.

Policy: transient failures (429, 5xx, explicit RetryableError) retry with
exponential backoff + full jitter, capped and attempt-bounded. Client errors
(4xx except 429) and explicit FatalError fail fast to the DLQ.
"""

from __future__ import annotations

import random


class RetryableError(Exception):
    """A transient failure — worth another attempt."""


class FatalError(Exception):
    """A permanent failure — send straight to the DLQ, do not retry."""


def http_retryable(status_code: int) -> bool:
    """429 and 5xx retry; other 4xx fail fast."""
    if status_code == 429:
        return True
    return 500 <= status_code < 600


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, FatalError):
        return False
    if isinstance(exc, RetryableError):
        return True
    # Unknown failures are treated as transient — better a bounded retry than a
    # dropped job; the attempt cap still guarantees it lands in the DLQ.
    return True


def compute_delay(
    attempt: int,
    *,
    base_delay: float,
    max_delay: float,
    rng: random.Random | None = None,
) -> float:
    """Full-jitter exponential backoff for the given retry attempt (1-based).

    Returns a delay in seconds uniformly sampled from ``[0, min(cap, base*2^(n-1))]``.
    """
    if attempt < 1:
        raise ValueError("attempt is 1-based")
    ceiling = min(max_delay, base_delay * (2 ** (attempt - 1)))
    r = rng or random
    return r.uniform(0.0, ceiling)


def should_retry(attempt: int, max_attempts: int, exc: BaseException) -> bool:
    """True if this job gets another attempt; False means route to the DLQ."""
    return attempt < max_attempts and is_retryable(exc)
