"""Classify a dead job's exception into a safe, human-facing failure reason.

A run fails when a job dies (``worker._handle_failure``). By that point the only
signal that survives is the raised exception's class and message — the SSRF-
critical ``fetch``/``egress`` code is deliberately NOT churned to attach
structured attributes (REQ-T5), so this module reads what is already there.

Security invariant (mirrors ``findings.engines.EngineError``): ``str(exc)`` is
persisted verbatim to ``run.error``, the DLQ, and the logs, and can embed
sensitive detail — ``egress.validate_target`` puts a resolved INTERNAL IP in its
message (``…resolves to a non-public address: 10.0.0.5``) and an engine's stderr
can echo scanned secrets. So this classifier NEVER copies the raw message into
the surfaced ``reason``: it emits curated per-category copy and returns only
values that are safe to show — a scope/target HOST (public, operator-facing) and
an HTTP status code. The raw message stays write-only in ``run.error`` for logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from recon.domain import RunStage
from recon.findings.engines import EngineError, EngineTimeout


class FailureCategory:
    """Coarse, stable wire buckets for a run failure (surfaced to the API/UI)."""

    OUT_OF_SCOPE = "out_of_scope"
    DNS_ERROR = "dns_error"
    BLOCKED_ADDRESS = "blocked_address"
    NOT_AUTHORIZED = "not_authorized"
    INVALID_TARGET = "invalid_target"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    HTTP_ERROR = "http_error"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    ENGINE_ERROR = "engine_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureInfo:
    """A classified, safe-to-surface failure. ``reason`` is curated copy (never the
    raw exception message); ``host``/``http_status`` are the only echoed context."""

    category: str
    reason: str
    host: str | None = None
    http_status: int | None = None


# The raise-site message formats this classifier keys on. If any of these strings
# is reworded at its source, ``failure_test.py`` (which drives the real raise
# sites) fails — production classification can't silently drift.
_HTTP_RE = re.compile(r"HTTP (\d{3})")
_SCOPE_RE = re.compile(r"not in engagement scope: (\S+)")
_DNS_RE = re.compile(r"DNS resolution failed for (\S+)")

_CAPTURE_HINT = (
    "If it is an auth-gated or bot-protected page, capture the JavaScript from a "
    "signed-in browser session with the capture extension."
)


def _access_denied_reason(status: int) -> str:
    label = "Forbidden" if status == 403 else "Unauthorized"
    return f"The target refused the request (HTTP {status} {label}). {_CAPTURE_HINT}"


def classify_failure(exc: BaseException, stage: RunStage | None = None) -> FailureInfo:
    """Bucket a dead-job exception into a safe :class:`FailureInfo`. Pure; no I/O."""
    # Engine failures are class-typed; keep their message/stderr OUT of `reason`.
    if isinstance(exc, EngineTimeout):
        return FailureInfo(FailureCategory.TIMEOUT, "An analysis engine timed out on the target.")
    if isinstance(exc, EngineError):
        return FailureInfo(FailureCategory.ENGINE_ERROR, "An analysis engine failed on the target.")

    msg = str(exc)

    http = _HTTP_RE.search(msg)
    if http:
        status = int(http.group(1))
        if status in (401, 403):
            return FailureInfo(
                FailureCategory.ACCESS_DENIED, _access_denied_reason(status), http_status=status
            )
        if status == 429:
            return FailureInfo(
                FailureCategory.RATE_LIMITED,
                "The target rate-limited the crawler (HTTP 429). Try again later, or "
                "capture the JavaScript from a signed-in browser session with the "
                "capture extension.",
                http_status=status,
            )
        if 500 <= status < 600:
            return FailureInfo(
                FailureCategory.SERVER_ERROR,
                f"The target returned a server error (HTTP {status}).",
                http_status=status,
            )
        return FailureInfo(
            FailureCategory.HTTP_ERROR, f"The target returned HTTP {status}.", http_status=status
        )

    scope = _SCOPE_RE.search(msg)
    if scope:
        host = scope.group(1)
        return FailureInfo(
            FailureCategory.OUT_OF_SCOPE,
            f"The crawl reached {host}, which is outside the engagement scope, so it "
            "was not fetched. Add it to the scope (or target it directly) and re-run.",
            host=host,
        )

    dns = _DNS_RE.search(msg)
    if dns:
        host = dns.group(1)
        return FailureInfo(
            FailureCategory.DNS_ERROR,
            f"The target host {host} could not be resolved (DNS lookup failed).",
            host=host,
        )

    # SSRF guard: NEVER echo the message — it embeds our resolved internal IP.
    if "non-public address" in msg or "no addresses resolved" in msg:
        return FailureInfo(
            FailureCategory.BLOCKED_ADDRESS,
            "The target resolved to a non-public address and was blocked by the egress guard.",
        )
    if "not authorized for egress" in msg:
        return FailureInfo(
            FailureCategory.NOT_AUTHORIZED,
            "This session is not authorized to fetch from the network.",
        )
    if any(
        s in msg
        for s in ("malformed URL", "scheme not allowed", "userinfo is not allowed", "missing host")
    ):
        return FailureInfo(
            FailureCategory.INVALID_TARGET,
            "The target URL is malformed or uses an unsupported scheme.",
        )
    if "deadline exceeded" in msg or "timed out" in msg:
        return FailureInfo(FailureCategory.TIMEOUT, "The target timed out while responding.")
    if "exceeds" in msg and "bytes" in msg:
        return FailureInfo(
            FailureCategory.TOO_LARGE, "The target response exceeded the fetch size limit."
        )

    # Fallback: a safe, generic reason — never the raw message (it may leak detail).
    where = f" during the {stage.value} stage" if stage is not None else ""
    return FailureInfo(FailureCategory.UNKNOWN, f"The run failed{where}.")
