"""Fetch stage — pull a single target asset through the egress guard (REQ-P2).

Given a run whose ``target`` is a URL, download it (subject to the scope + SSRF
guard in ``egress``) and store it as the run's input blob, so the existing
analyze path runs over fetched content exactly as it does over an upload.

Security posture (all validated by the egress design review):
- Every request AND every redirect hop is re-validated by ``egress.validate_target``
  (scheme + in-scope host + all-IPs-public).
- The connection is pinned to the pre-validated IP for the duration of the send,
  defeating DNS rebinding between the check and httpx's own connect. The pin
  overrides the process-global ``socket.getaddrinfo``; this is safe ONLY because
  a worker runs stages sequentially in one thread (``worker.run_once`` /
  ``serve_forever``). A future threaded/async worker MUST switch to a pinned
  transport instead.
- Redirects are handled manually (``follow_redirects=False``); the body is read
  streamed with a decoded-byte cap and an overall wall-clock deadline (httpx's
  read timeout only bounds the gap between chunks, not total time).

OS/network-level egress isolation is deferred (see egress module docstring).
"""

from __future__ import annotations

import contextlib
import socket
import time
from typing import Iterator
from urllib.parse import urljoin, urlsplit

import httpx
from redis import Redis
from sqlalchemy import update

from recon import storage
from recon.config import Settings, get_settings
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import AssetStatus
from recon.fetch import egress, politeness
from recon.observability import get_logger
from recon.progress import heartbeat as progress
from recon.queue import retry
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries
from recon.sessions import service as sessions_service

log = get_logger("recon.fetch")

_MAX_REDIRECTS = 5


@contextlib.contextmanager
def _pin_dns(host: str, ips: tuple[str, ...]) -> Iterator[None]:
    """Pin ``host`` to the already-validated ``ips`` for the wrapped block.

    Process-global override of ``socket.getaddrinfo`` (which httpx's sync backend
    calls at connect time) — see the module docstring's single-thread invariant.
    Fails CLOSED: a lookup for any name other than the pinned host during the send
    is unexpected (no proxy, one host per hop) and is blocked rather than resolved,
    so a validator/client host-parse divergence can never reach un-pinned DNS."""
    real_getaddrinfo = socket.getaddrinfo
    host_lower = host.lower()

    def pinned(node, service, *args, **kwargs):
        if (node or "").lower() != host_lower:
            raise egress.EgressBlocked(f"unexpected DNS lookup during fetch: {node!r}")
        results = []
        for ip in ips:
            if ":" in ip:
                results.append(
                    (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, service, 0, 0))
                )
            else:
                results.append(
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, service))
                )
        return results

    socket.getaddrinfo = pinned
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo


def fetch_url(
    url: str,
    scope_hosts: list[str],
    *,
    timeout_s: float,
    max_bytes: int,
    max_redirects: int = _MAX_REDIRECTS,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Fetch ``url`` under the egress policy and return its bytes.

    Raises :class:`egress.EgressBlocked` (scope/SSRF), :class:`retry.FatalError`
    (deterministic: bad status, too large, too many redirects — do not retry), or
    :class:`retry.RetryableError` (429/5xx, deadline — worth another attempt)."""
    deadline = time.monotonic() + timeout_s
    current = url
    with httpx.Client(
        follow_redirects=False, timeout=httpx.Timeout(timeout_s), transport=transport
    ) as client:
        for _hop in range(max_redirects + 1):
            target = egress.validate_target(current, scope_hosts)  # scope + IP, every hop
            # Pin/validate on the SAME host httpx will connect to — a parser split
            # between urlsplit (validator) and httpx.URL (client) must fail closed.
            if httpx.URL(current).host.lower() != target.host.lower():
                raise egress.EgressBlocked(
                    f"URL host parse mismatch: {httpx.URL(current).host} vs {target.host}"
                )
            with _pin_dns(target.host, target.ips):
                # identity encoding so a decoded-byte cap == received-byte cap.
                with client.stream(
                    "GET", current, headers={"Accept-Encoding": "identity"}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise retry.FatalError("redirect without a Location header")
                        current = urljoin(current, location)  # resolves relative / //host
                        continue
                    if not 200 <= response.status_code < 300:
                        # 429/5xx are worth a retry; other statuses (4xx, non-redirect
                        # 3xx) are deterministic and fail fast.
                        message = f"target returned HTTP {response.status_code}"
                        if retry.http_retryable(response.status_code):
                            # Honor the target's own backoff ask (REQ-Q3) when present.
                            retry_after = _parse_retry_after(response.headers.get("retry-after"))
                            raise retry.RetryableError(message, retry_after=retry_after)
                        raise retry.FatalError(message)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise retry.RetryableError("overall fetch deadline exceeded")
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise retry.FatalError(f"response exceeds {max_bytes} bytes")
                    return bytes(body)
    raise retry.FatalError(f"exceeded {max_redirects} redirects")


def fetch_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None = None) -> None:
    """Fetch the run's asset(s) into their input blob(s).

    A crawl run (``run_asset`` rows present, Slice Y) loops every not-yet-terminal
    asset through ``_fetch_assets``: idempotent per asset (a terminal row is
    skipped, never re-fetched), paced per host (REQ-Q3), best-effort (one asset's
    failure does not abort the run), heartbeating, and cooperatively interruptible
    (REQ-A4).

    An upload/single-URL run (no ``run_asset`` rows) falls through unchanged to
    the legacy path below: fetch ``run.target`` into ``run.input_ref``, a no-op
    when there is no target or it was already fetched (idempotent across a stage
    retry)."""
    rows = run_assets.list_for_run(tenant_id, run_id)
    if rows:
        _fetch_assets(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, rows=rows)
        return
    # ---- legacy single-target path below (unchanged) ----
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        target = run.target if run is not None else None
        input_ref = run.input_ref if run is not None else None
        session_id = str(run.session_id) if run is not None else None
    # Nothing to do if already fetched (idempotent), or the target isn't a
    # fetchable http(s) URL — `target` may be a bare scope label (e.g. "acme.io"),
    # which is not something to fetch.
    if input_ref or not target or urlsplit(target).scheme.lower() not in ("http", "https"):
        return

    engagement = sessions_service.get_session(tenant_id, session_id)
    # Defense-in-depth: scope is always taken live from the session (REQ-P2), and
    # egress requires the session's authorization ack (REQ-P3).
    if engagement is None or not engagement.authorization_ack:
        raise egress.EgressBlocked("session is not authorized for egress")

    settings = get_settings()
    # Politeness gate (REQ-Q3): never hammer one target, and stay under a global
    # fetch budget. A throttle defers the whole fetch via retry backoff rather than
    # blocking the worker (which does not heartbeat mid-fetch). A hostless/malformed
    # target skips the gate (no shared empty-host bucket) — fetch_url's egress
    # validation rejects it deterministically a moment later.
    host = (urlsplit(target).hostname or "").lower()
    if host:
        wait = politeness.check(redis, host, settings=settings)
        if wait > 0:
            raise retry.RetryableError(
                f"fetch throttled for host {host!r}; retry in {wait:.1f}s", retry_after=wait
            )
    try:
        content = fetch_url(
            target,
            engagement.scope_hosts,
            timeout_s=settings.fetch_timeout_seconds,
            max_bytes=settings.max_fetch_bytes,
        )
    except egress.EgressBlocked as exc:
        # Scope/SSRF/scheme blocks are deterministic — fail fast, don't burn retries.
        raise retry.FatalError(str(exc)) from exc
    key = storage.put_blob(tenant_id, run_id, "input", content)
    with tenant_session(tenant_id) as session:
        session.execute(update(Run).where(Run.id == run_id).values(input_ref=key))
    log.info("fetch.done", run_id=run_id, bytes=len(content))


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header's delta-seconds form (e.g. ``"30"``).

    The HTTP-date form is intentionally not handled — it is rare for 429s and a
    stale clock could yield a negative/huge wait; absent a parse we fall back to
    the normal exponential backoff, which is safe."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _await_host_slot(
    redis: Redis, host: str, *, tenant_id: str, run_id: str, job_id: str | None,
    settings: Settings,
) -> None:
    """Acquire the per-host politeness slot, re-checking until ``check()`` yields it.

    ``politeness.check`` is a CONSUMING acquire (REQ-Q3): it returns ``0.0`` ONLY to
    the caller that actually took the host slot (and incremented the global
    budget). A sleep-once-then-proceed would fetch WITHOUT ever having taken that
    slot — defeating the anti-hammer guarantee for every asset after the first on
    a host — so this re-checks in a loop, heartbeating through each wait so the
    job's lease survives a long throttle."""
    while (wait := politeness.check(redis, host, settings=settings)) > 0:
        _beat_sleep(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, seconds=wait)


def _beat_sleep(
    redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None, seconds: float
) -> None:
    """Sleep ``seconds``, heartbeating the job lease once per full interval elapsed.

    A wait shorter than one heartbeat interval can't threaten the (much longer)
    ``heartbeat_stall_threshold_seconds``, so it sleeps quietly without a write;
    a wait spanning one or more full intervals heartbeats along the way, so a long
    politeness/Retry-After backoff can never starve the job's lease mid-wait."""
    remaining = seconds
    step = get_settings().crawl_heartbeat_interval_seconds
    while remaining > 0:
        nap = min(step, remaining)
        time.sleep(nap)
        remaining -= nap
        if job_id and remaining > 0:
            progress.beat(
                redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                done=0, total=0, emit_event=False,
            )


def _fetch_assets(
    redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None,
    rows: list[run_assets.AssetRow],
) -> None:
    """Fetch every not-yet-terminal asset of a crawl run (REQ-C1/D5), best-effort.

    Each asset's terminal status commits in ITS OWN transaction (not one loop-wide
    one) so an infra error mid-loop can never roll back an earlier asset's commit —
    that per-asset commit is what makes a redelivery idempotent (skip, don't
    re-fetch, don't double-egress). A control interrupt (REQ-A4) is checked at the
    top of every iteration, before any fetch attempt, and propagates straight out
    of this loop (never caught here — it is not a failure)."""
    engagement = _authorized_engagement(tenant_id, run_id)
    settings = get_settings()
    total = len(rows)
    terminal = (AssetStatus.OK.value, AssetStatus.FAILED.value)
    for i, asset in enumerate(rows, 1):
        if asset.fetch_status in terminal or asset.input_ref:
            continue  # terminal already — idempotent redelivery skip
        run_queries.raise_if_control_requested(tenant_id, run_id)  # REQ-A4
        host = (urlsplit(asset.url).hostname or "").lower()
        if host:
            _await_host_slot(
                redis, host, tenant_id=tenant_id, run_id=run_id, job_id=job_id, settings=settings
            )
        try:
            content = fetch_url(
                asset.url, engagement.scope_hosts,
                timeout_s=settings.fetch_timeout_seconds, max_bytes=settings.max_fetch_bytes,
            )
        except (egress.EgressBlocked, retry.FatalError, retry.RetryableError) as exc:
            with tenant_session(tenant_id) as s:
                run_assets.set_fetch_failed(s, asset.id, str(exc))  # per-asset commit
            log.warning("fetch.asset_failed", run_id=run_id, url=asset.url, error=str(exc))
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:  # honor the target's host-wide backoff even though we drop it
                _beat_sleep(
                    redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                    seconds=float(retry_after),
                )
            continue
        key = storage.put_blob(tenant_id, run_id, "input", content)
        with tenant_session(tenant_id) as s:
            run_assets.set_fetch_ok(s, asset.id, key)  # per-asset commit
        if job_id:
            progress.beat(
                redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, done=i, total=total
            )
        log.info("fetch.asset_done", run_id=run_id, url=asset.url, bytes=len(content))


def _authorized_engagement(tenant_id: str, run_id: str) -> sessions_service.SessionView:
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        session_id = str(run.session_id) if run is not None else None
    engagement = sessions_service.get_session(tenant_id, session_id)
    if engagement is None or not engagement.authorization_ack:
        raise retry.FatalError("session is not authorized for egress")
    return engagement
