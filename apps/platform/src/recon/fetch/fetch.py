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
import json
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from redis import Redis
from sqlalchemy import update

from recon import storage
from recon.config import Settings, clamp_fetch_bytes, get_settings
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import AssetStatus
from recon.events.log import record_event
from recon.fetch import egress, politeness
from recon.findings import chunkenum, sourcemapper
from recon.observability import get_logger
from recon.progress import heartbeat as progress
from recon.queue import retry
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries
from recon.sessions import service as sessions_service

log = get_logger("recon.fetch")

_MAX_REDIRECTS = 5

# Present a browser-shaped request. The default python-httpx User-Agent trips
# naive bot filters — e.g. Cloudflare bot-fight at default settings 403s it — which
# blocks otherwise-public assets; a realistic UA + Accept headers clears those
# gates. It does NOT solve a JS/managed challenge (that needs a real browser —
# headless/extension). Accept-Encoding stays "identity" so the streamed
# decoded-byte cap still equals the received-byte cap (see fetch_url).
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
}

# Allowlisted response headers for tech detection (case-insensitive). A T1 privacy
# control: the VALUE of any header NOT allowlisted here is discarded, so we custody only
# tech-identifying signal — never credentials or user data. Set-Cookie contributes
# cookie NAMES only (`_cookie_names`); Authorization / Cookie / Proxy-Authorization are
# never persisted. The widened set (DEBT D22) adds vendor/CDN/CMS identifiers plus
# CSP/CORS, whose values describe the target's ARCHITECTURE (framework, edge, allowed
# origins/hostnames) — the recon signal we want, not secrets. Shared with the capture
# stage (Task 7) via `fetch._HEADER_ALLOWLIST` / `_allowlisted_headers` so both signal
# producers stay in lockstep — one allowlist, not two.
_HEADER_ALLOWLIST = frozenset(
    {
        "server",
        "x-powered-by",
        "x-aspnet-version",
        "x-aspnetmvc-version",
        "x-generator",
        "x-drupal-dynamic-cache",
        "x-drupal-cache",
        "via",
        "x-varnish",
        "cf-ray",
        "x-amz-cf-id",
        "x-served-by",
        "x-shopify-stage",
        "x-github-request-id",
        # DEBT D22 widening — vendor/CDN/CMS identifiers + CSP/CORS architecture signal.
        # Every value is a product/protocol name or hostname, never a credential.
        # `www-authenticate` is narrowed to its scheme token in `_allowlisted_headers`;
        # CORS `access-control-allow-*` is kept via the prefix rule below. (`link` is
        # deliberately NOT here — its URLs can carry signed-CDN query tokens, REQ-S2/S4.)
        "content-security-policy",
        "content-security-policy-report-only",
        "www-authenticate",
        "powered",
        "powered-by",
        "x-powered-cms",
        "platform",
        "x-cdn",
        "x-servedby",
        "x-turbo-charged-by",
        "x-litespeed-cache",
        "x-cache",
        "alt-svc",
        "vary",
    }
)

# Header-value prefixes kept in full — vendor families we can't enumerate by exact key:
# Fastly debug headers and all CORS `access-control-allow-*` (origin / methods / headers
# / credentials; values are origins, header NAMES, or a bool — not secrets; `access-
# control-max-age` / `-expose-headers` don't start with `-allow-` and stay excluded).
_HEADER_ALLOWLIST_PREFIXES = ("x-fastly-", "access-control-allow-")

# Size bound on any single stored header value so a multi-KB CSP/header can't bloat the
# fingerprint-signal blob. NOT a privacy control (a report-uri/nonce can sit before the
# cut — the allowlist is the privacy gate); a generous bound vs a normal header block.
_HEADER_VALUE_MAX_CHARS = 16384


@dataclass(frozen=True)
class _FetchedResponse:
    """The shared hop-core's result: bytes plus the data the fingerprint-signal
    harvest needs (T5's ``fetch_url`` stays a bytes-only wrapper over this)."""

    body: bytes
    status: int
    headers: dict[str, str]  # final response headers, lowercased keys
    set_cookie: list[str]  # raw Set-Cookie lines; NAMES extracted by _cookie_names


def _allowlisted_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep only allowlisted header VALUES (T1); ``headers`` keys must already be
    lowercased (see ``_FetchedResponse.headers``). The single choke point for BOTH the
    fetch and capture signal producers, so the privacy narrowing + size bound apply
    everywhere:
    - ``www-authenticate`` is reduced to its auth SCHEME token (never the full
      challenge — an NTLM/Negotiate type-2 value custodies internal names + a nonce, and
      every enthec pattern anchors on the scheme alone).
    - each kept value is truncated to ``_HEADER_VALUE_MAX_CHARS`` (a size bound)."""
    kept = {name: headers[name] for name in _HEADER_ALLOWLIST if name in headers}
    kept.update({k: v for k, v in headers.items() if k.startswith(_HEADER_ALLOWLIST_PREFIXES)})
    if "www-authenticate" in kept:
        scheme = kept["www-authenticate"].split(maxsplit=1)
        kept["www-authenticate"] = scheme[0] if scheme else ""
    return {k: v[:_HEADER_VALUE_MAX_CHARS] for k, v in kept.items()}


def _cookie_names(set_cookie_lines: list[str]) -> list[str]:
    """Cookie NAMES only, never values (T1) — the token before the first '='."""
    names = {line.split("=", 1)[0].strip() for line in set_cookie_lines if "=" in line}
    return sorted(n for n in names if n)


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
                    (
                        socket.AF_INET6,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (ip, service, 0, 0),
                    )
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


class _TransientStatus(retry.RetryableError):
    """A 429/5xx worth a bounded per-asset retry INSIDE the crawl fetch loop (DEBT D20).

    A fetch-local subclass rather than a flag on the domain-agnostic
    ``retry.RetryableError`` so the retry decision is a local ``isinstance`` check: the
    multi-asset loop retries ``_TransientStatus`` but NOT a bare ``RetryableError`` (e.g.
    "overall fetch deadline exceeded" — the time budget is already spent, not worth
    re-burning). It stays a ``RetryableError`` subclass, so every existing consumer is
    unaffected: the loop's failure handler (`except (..., RetryableError)`), the queue's
    ``is_retryable``/``should_retry``, and ``classify_failure``'s ``str(exc)`` regex all
    still see a retryable error carrying the same message + ``retry_after``."""


def _fetch_hops(
    url: str,
    scope_hosts: list[str],
    *,
    timeout_s: float,
    max_bytes: int,
    max_redirects: int = _MAX_REDIRECTS,
    allow_local: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> _FetchedResponse:
    """Fetch ``url`` under the full egress policy and return its bytes, status, and
    final-hop headers/Set-Cookie lines — the SHARED validated-hop core. ``fetch_url``
    is a thin bytes-only wrapper over this (its public signature and behavior are
    UNCHANGED; the SSRF crown jewel is not churned, T5).

    Raises :class:`egress.EgressBlocked` (scope/SSRF), :class:`retry.FatalError`
    (deterministic: bad status, too large, too many redirects — do not retry), or
    :class:`retry.RetryableError` (429/5xx, deadline — worth another attempt)."""
    deadline = time.monotonic() + timeout_s
    current = url
    with httpx.Client(
        follow_redirects=False, timeout=httpx.Timeout(timeout_s), transport=transport
    ) as client:
        for _hop in range(max_redirects + 1):
            target = egress.validate_target(  # scope + IP, every hop
                current, scope_hosts, allow_local=allow_local
            )
            # Pin/validate on the SAME host httpx will connect to — a parser split
            # between urlsplit (validator) and httpx.URL (client) must fail closed.
            if httpx.URL(current).host.lower() != target.host.lower():
                raise egress.EgressBlocked(
                    f"URL host parse mismatch: {httpx.URL(current).host} vs {target.host}"
                )
            # Pin DNS on the validated host and stream within one scope (enter order
            # = pin then stream; exit reverses). Browser-shaped headers (see
            # _FETCH_HEADERS); identity encoding so a decoded-byte cap == received-byte cap.
            with (
                _pin_dns(target.host, target.ips),
                client.stream("GET", current, headers=_FETCH_HEADERS) as response,
            ):
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
                        # `_TransientStatus` (a RetryableError subclass) marks this as a
                        # 429/5xx the per-asset loop MAY retry (DEBT D20) — distinct from
                        # the deadline RetryableError below, which it never retries.
                        retry_after = _parse_retry_after(response.headers.get("retry-after"))
                        raise _TransientStatus(message, retry_after=retry_after)
                    raise retry.FatalError(message)
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise retry.RetryableError("overall fetch deadline exceeded")
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise retry.FatalError(f"response exceeds {max_bytes} bytes")
                return _FetchedResponse(
                    body=bytes(body),
                    status=response.status_code,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    set_cookie=list(response.headers.get_list("set-cookie")),
                )
    raise retry.FatalError(f"exceeded {max_redirects} redirects")


def fetch_url(
    url: str,
    scope_hosts: list[str],
    *,
    timeout_s: float,
    max_bytes: int,
    max_redirects: int = _MAX_REDIRECTS,
    allow_local: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Fetch ``url`` under the egress policy and return its bytes. Thin wrapper over
    ``_fetch_hops`` — signature and behavior unchanged (T5)."""
    return _fetch_hops(
        url,
        scope_hosts,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        allow_local=allow_local,
        transport=transport,
    ).body


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
        max_fetch = run.max_fetch_bytes if run is not None else None
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
            max_bytes=clamp_fetch_bytes(max_fetch, settings),
            allow_local=settings.allow_local_egress,
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
    redis: Redis,
    host: str,
    *,
    tenant_id: str,
    run_id: str,
    job_id: str | None,
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
                redis,
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                done=0,
                total=0,
                emit_event=False,
            )


def _fetch_asset_with_retry(
    redis: Redis,
    *,
    asset_url: str,
    scope_hosts: list[str],
    max_bytes: int,
    i: int,
    total: int,
    tenant_id: str,
    run_id: str,
    job_id: str | None,
    settings: Settings,
) -> _FetchedResponse:
    """Fetch one crawl asset, retrying a transient 429/5xx a bounded number of times
    (DEBT D20) so one flaky asset does not drop to ``failed`` (→ run PARTIAL).

    Fail-closed lease invariant: EVERY attempt heartbeats BEFORE its ``_fetch_hops``
    call, so — as long as the host-slot wait stays short — the gap between lease
    renewals stays one fetch (<= fetch_timeout_seconds) plus one bounded backoff
    (<= fetch_asset_retry_max_delay_seconds), under heartbeat_stall_threshold_seconds.
    Without a per-attempt beat a retry sequence would outrun the lease and let a peer
    reclaim the RUNNING job and double-fetch (the double-egress this loop prevents).
    (``_beat_sleep`` does not itself beat a sub-heartbeat-interval host-slot wait, so a
    heavily-contended global fetch budget narrows that margin — a pre-existing
    heartbeat-family caveat tracked in DEBT D20; in practice the retry re-hits the
    just-5xx'd host, whose own min-interval keeps that wait ~1s.) Only attempt 1 emits
    a progress event; retries renew the lease silently.

    Retries ONLY ``_TransientStatus`` (429/5xx). ``egress.EgressBlocked`` /
    ``retry.FatalError`` and a bare ``retry.RetryableError`` (e.g. "overall fetch
    deadline exceeded" — the budget is already spent) are NOT caught here; they
    propagate to the loop's per-asset failure handler unchanged. On exhausting the
    attempts the final ``_TransientStatus`` is re-raised, so that handler marks the
    asset failed and honors any host-wide ``retry_after`` exactly as before.
    Per-attempt ``_await_host_slot`` keeps REQ-Q3 politeness; the retry is synchronous
    in the worker thread, so the DNS-pin single-thread invariant holds."""
    host = (urlsplit(asset_url).hostname or "").lower()
    # <=0 disables the retry (one try, pre-D20 behavior); clamp a misconfigured
    # negative to 0 so the loop always makes at least the initial attempt.
    attempts = max(0, settings.fetch_asset_retry_attempts)
    cap = settings.fetch_asset_retry_max_delay_seconds
    for attempt in range(1, attempts + 2):
        if job_id:
            progress.beat(
                redis,
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                done=i,
                total=total,
                emit_event=attempt == 1,
            )
        if host:
            _await_host_slot(
                redis, host, tenant_id=tenant_id, run_id=run_id, job_id=job_id, settings=settings
            )
        try:
            return _fetch_hops(
                asset_url,
                scope_hosts,
                timeout_s=settings.fetch_timeout_seconds,
                max_bytes=max_bytes,
                allow_local=settings.allow_local_egress,
            )
        except _TransientStatus as exc:
            if attempt > attempts:
                raise  # retries exhausted — the loop's handler marks the asset failed
            delay = min(
                max(
                    retry.compute_delay(
                        attempt, base_delay=settings.retry_base_delay_seconds, max_delay=cap
                    ),
                    exc.retry_after or 0.0,
                ),
                cap,
            )
            log.info(
                "fetch.asset_retry",
                run_id=run_id,
                url=asset_url,
                attempt=attempt,
                delay=round(delay, 2),
                reason=str(exc),
            )
            # Observe a pause/cancel BETWEEN retries (REQ-A4) so a flaky asset can't
            # delay honoring it by the whole retry budget. ControlInterrupt is not in
            # the loop's failure `except`, so it propagates out like the top-of-loop
            # check rather than being mistaken for a fetch failure.
            run_queries.raise_if_control_requested(tenant_id, run_id)
            _beat_sleep(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, seconds=delay)
    raise RuntimeError("unreachable: _fetch_asset_with_retry must return or raise")


def _fetch_assets(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    job_id: str | None,
    rows: list[run_assets.AssetRow],
) -> None:
    """Fetch every not-yet-terminal asset of a crawl run (REQ-C1/D5), best-effort.

    Each asset's terminal status commits in ITS OWN transaction (not one loop-wide
    one) so an infra error mid-loop can never roll back an earlier asset's commit —
    that per-asset commit is what makes a redelivery idempotent (skip, don't
    re-fetch, don't double-egress). A control interrupt (REQ-A4) is checked at the
    top of every iteration, before any fetch attempt, and propagates straight out
    of this loop (never caught here — it is not a failure).

    Every processed asset (not skipped) is fetched via ``_fetch_asset_with_retry``,
    which heartbeats BEFORE every attempt — its bounded 429/5xx retry (DEBT D20)
    included — so the gap between lease renewals stays one ``_fetch_hops`` call
    (<= fetch_timeout_seconds) plus a short host-slot wait plus one bounded backoff,
    safely under heartbeat_stall_threshold_seconds. Without a per-attempt beat, a
    slow-then-fail asset or a retry sequence would go unheartbeated long enough for a
    peer worker to reclaim the stream message and double-fetch the remaining assets —
    exactly what the politeness gate exists to prevent.

    Every successfully-fetched asset also folds its allowlisted headers + cookie
    NAMES + script URL into an in-memory ``signal`` dict (T1), written as ONE
    ``fingerprint-signal`` blob after this loop (T6) rather than per asset — a
    per-asset write would mean "latest blob" drops every earlier asset's signal.
    NOTE: because ``signal`` is local to THIS call, a redelivered/retried
    invocation (a crash or lease-timeout mid-crawl, per the idempotency note
    above) skips already-terminal assets without re-harvesting them, so its own
    end-of-call write reflects only the assets fetched in that invocation — a
    known limitation of "one write per call", not per logical run, tracked as
    follow-up debt rather than solved here."""
    engagement, run_cap = _authorized_engagement(tenant_id, run_id)
    settings = get_settings()
    cap = clamp_fetch_bytes(run_cap, settings)
    total = len(rows)
    terminal = (AssetStatus.OK.value, AssetStatus.FAILED.value)
    signal: dict[str, dict] = {}
    for i, asset in enumerate(rows, 1):
        if asset.fetch_status in terminal or asset.input_ref:
            continue  # terminal already — idempotent redelivery skip
        run_queries.raise_if_control_requested(tenant_id, run_id)  # REQ-A4
        try:
            fetched = _fetch_asset_with_retry(
                redis,
                asset_url=asset.url,
                scope_hosts=engagement.scope_hosts,
                max_bytes=cap,
                i=i,
                total=total,
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                settings=settings,
            )
            content = fetched.body
        except (egress.EgressBlocked, retry.FatalError, retry.RetryableError) as exc:
            with tenant_session(tenant_id) as s:
                run_assets.set_fetch_failed(s, asset.id, str(exc))  # per-asset commit
            log.warning("fetch.asset_failed", run_id=run_id, url=asset.url, error=str(exc))
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:  # honor the target's host-wide backoff even though we drop it
                _beat_sleep(
                    redis,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    job_id=job_id,
                    seconds=float(retry_after),
                )
            continue
        _harvest_signal(signal, asset.url, fetched)  # accumulate the per-host signal (T1/T11)
        key = storage.put_blob(tenant_id, run_id, "input", content)
        # Best-effort external source-map recovery (REQ-CE2). JS-SUCCESS path only,
        # inside _fetch_and_store_source_map's own non-re-raising try/except, so a
        # bad/blocked .map can NEVER reach the outer handler and mark the asset
        # fetch_failed (which would drop its JS finding). The .map GET runs with no
        # DB session open (mirrors the input put_blob); the ref then commits together
        # with fetch_ok in the one tx below.
        map_result = (
            _fetch_and_store_source_map(
                redis,
                js=content,
                asset_url=asset.url,
                scope_hosts=engagement.scope_hosts,
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                done=i,
                total=total,
                settings=settings,
                # D32-A1: the .map gets its OWN (larger) cap — a real source map is
                # 3-6x its bundle, so the shared `cap` above soft-drops it.
                max_bytes=settings.max_source_map_bytes,
            )
            if settings.crawl_fetch_source_maps
            else _NO_SOURCE_MAP
        )
        with tenant_session(tenant_id) as s:
            run_assets.set_fetch_ok(s, asset.id, key)  # per-asset commit
            if map_result.ref:
                run_assets.set_source_map_ref(s, asset.id, map_result.ref)
            elif map_result.skipped:
                # D32 honesty: a referenced .map we couldn't retrieve is a real coverage
                # gap (analyze -> source_map:"skipped"). Flag the asset AND record a
                # durable event (recorded, not published — like fingerprint.signal;
                # committed in this same fetch_ok tx so a redelivery, which skips an
                # already-terminal asset, never re-records).
                run_assets.set_source_map_skipped(s, asset.id)
                record_event(
                    s,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    event_type="fetch.source_map_skipped",
                    payload={
                        "url": asset.url,
                        "map_url": map_result.map_url,
                        "reason": map_result.reason,
                    },
                )
        # Best-effort webpack lazy-chunk enumeration (P4). Runs AFTER the parent asset is
        # committed fetch_ok, in its own non-re-raising helper, so a bad/blocked chunk can
        # never fail the parent JS asset (same invariant as the .map recovery above).
        chunks = (
            _enumerate_and_seed_chunks(
                redis,
                js=content,
                asset_url=asset.url,
                scope_hosts=engagement.scope_hosts,
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                done=i,
                total=total,
                settings=settings,
                max_bytes=cap,
            )
            if settings.crawl_enumerate_chunks
            else 0
        )
        log.info(
            "fetch.asset_done",
            run_id=run_id,
            url=asset.url,
            bytes=len(content),
            source_map=bool(map_result.ref),
            chunks=chunks,
        )
    _write_fingerprint_signal(redis, tenant_id=tenant_id, run_id=run_id, signal=signal)


def _harvest_signal(signal: dict[str, dict], asset_url: str, fetched: _FetchedResponse) -> None:
    """Fold one asset's allowlisted headers + cookie names + script URL into the
    per-host signal (T1). Host comes from the OBSERVED asset URL, never
    ``session.scope_hosts`` (T11) — a scope entry can be a wildcard/parent domain
    that is not itself a host anything was actually fetched from."""
    host = (urlsplit(asset_url).hostname or "").lower()
    if not host:
        return
    entry = signal.setdefault(host, {"headers": {}, "scripts": [], "meta": [], "cookies": []})
    entry["headers"].update(_allowlisted_headers(fetched.headers))
    for name in _cookie_names(fetched.set_cookie):
        if name not in entry["cookies"]:
            entry["cookies"].append(name)
    if asset_url not in entry["scripts"]:
        entry["scripts"].append(asset_url)


def _write_fingerprint_signal(
    redis: Redis, *, tenant_id: str, run_id: str, signal: dict[str, dict]
) -> None:
    """Persist ONE per-run fingerprint-signal blob + index it with a durable
    ``fingerprint.signal`` event (consolidated once per call — T6; see the
    redelivery caveat on ``_fetch_assets``). Recorded, not published: the sole
    consumer is the analyze fingerprint pass reading the durable log, not the
    live SSE feed."""
    if not signal:
        return
    signal_ref = storage.put_blob(
        tenant_id, run_id, "fingerprint-signal", json.dumps(signal).encode("utf-8")
    )
    with tenant_session(tenant_id) as session:
        record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="fingerprint.signal",
            payload={"signal_ref": signal_ref, "hosts": len(signal)},
        )


@dataclass(frozen=True)
class _SourceMapResult:
    """Outcome of the best-effort ``.map`` fetch (D32). ``ref`` is the stored blob key
    on success. ``skipped`` is True when a map was REFERENCED but soft-missed
    (oversized past the byte cap, 404, blocked, or malformed) — an honest coverage gap,
    distinct from "no map referenced" (every field falsy). ``map_url``/``reason`` carry
    the skip detail for the durable ``fetch.source_map_skipped`` event. ``ref`` and
    ``skipped`` are mutually exclusive per fetch attempt."""

    ref: str | None = None
    skipped: bool = False
    map_url: str | None = None
    reason: str | None = None


_NO_SOURCE_MAP = _SourceMapResult()


def _fetch_and_store_source_map(
    redis: Redis,
    *,
    js: bytes,
    asset_url: str,
    scope_hosts: list[str],
    tenant_id: str,
    run_id: str,
    job_id: str | None,
    done: int,
    total: int,
    settings: Settings,
    max_bytes: int,
) -> _SourceMapResult:
    """Best-effort: if ``js`` references an external ``//# sourceMappingURL=``, fetch
    that ``.map`` THROUGH THE EGRESS GUARD and store it, returning a ``_SourceMapResult``
    (``ref`` on success, ``skipped`` on a soft miss, all-falsy when no map is
    referenced). REQ-CE2.

    NEVER raises: a missing/blocked/oversized/malformed map is a soft miss (analyze
    falls back to the minified bundle) and must never propagate to the caller's outer
    handler, which would mark the asset fetch_failed and DROP its JS finding. The
    ``.map`` GET runs with NO DB session open (mirrors the input put_blob); the caller
    links the returned key / records the skip in the same tx that marks the asset
    fetch_ok."""
    map_url: str | None = None
    try:
        ref = sourcemapper.external_map_url(js.decode("utf-8", "replace"))
        if not ref:
            return _NO_SOURCE_MAP
        map_url = urljoin(asset_url, ref)
        # A SECOND outbound request for this asset — preserve the per-asset heartbeat
        # + politeness invariant (see _fetch_assets docstring): renew the job lease
        # and take a host slot before the .map GET, exactly as the JS fetch does.
        if job_id:
            progress.beat(
                redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, done=done, total=total
            )
        host = (urlsplit(map_url).hostname or "").lower()
        if host:
            _await_host_slot(
                redis, host, tenant_id=tenant_id, run_id=run_id, job_id=job_id, settings=settings
            )
        map_bytes = fetch_url(
            map_url,
            scope_hosts,
            timeout_s=settings.fetch_timeout_seconds,
            max_bytes=max_bytes,
            allow_local=settings.allow_local_egress,
        )
        return _SourceMapResult(ref=storage.put_blob(tenant_id, run_id, "source_map", map_bytes))
    except Exception as exc:  # noqa: BLE001 — soft miss; a bad map must never fail the asset
        # D32: a REFERENCED map we couldn't retrieve (map_url set) is an honest coverage
        # gap the caller records durably; a decode/parse failure before ref resolution
        # (map_url None) is still surfaced as a skip rather than silently dropped.
        log.info(
            "fetch.source_map_skipped",
            run_id=run_id,
            url=asset_url,
            map_url=map_url,
            error=str(exc),
        )
        return _SourceMapResult(skipped=True, map_url=map_url, reason=str(exc))


def _enumerate_and_seed_chunks(
    redis: Redis,
    *,
    js: bytes,
    asset_url: str,
    scope_hosts: list[str],
    tenant_id: str,
    run_id: str,
    job_id: str | None,
    done: int,
    total: int,
    settings: Settings,
    max_bytes: int,
) -> int:
    """Best-effort: statically enumerate a webpack bundle's lazy-chunk URLs from ``js``
    (the ``__webpack_require__.u`` builder — NO execution, ``recon.findings.chunkenum``),
    fetch each THROUGH THE EGRESS GUARD, and seed it as an already-fetched (OK) asset so
    the analyze stage recovers its endpoints. Returns the number of chunks seeded.

    NEVER raises: like the source-map path, a bad/blocked/oversized chunk is a soft miss
    that must not reach the caller's outer handler (which would mark the PARENT asset
    fetch_failed and drop its JS finding). Security posture:
    - Content-derived, therefore UNTRUSTED: each chunk URL is fetched only via
      ``fetch_url`` (``egress.validate_target`` on every hop), so an out-of-scope chunk
      raises ``EgressBlocked`` and is DROPPED — scope is never widened (mirrors the
      crawl's ``_revalidate``).
    - Capped: the discover-time ``crawl_max_assets`` ceiling does not cover fetch-time
      seeding, so it is RE-APPLIED here against the live ``run_asset`` count — a hostile
      or huge chunk map cannot flood the run.
    - Polite: each extra GET renews the job lease (``progress.beat``) and takes a host
      slot BEFORE the request, preserving the per-asset heartbeat invariant so a peer
      cannot reclaim the stream and double-fetch (see the ``_fetch_assets`` docstring).
    - Interruptible (REQ-A4): a pause/cancel is honored BEFORE each chunk fetch
      (``raise_if_control_requested``) and the resulting ``ControlInterrupt`` is re-raised
      past the soft-miss guard; the ``finally`` seeds whatever was already fetched so an
      interrupt mid-burst never orphans a blob (mirrors the main loop's per-asset commit
      before its per-asset control check)."""
    if b"webpack" not in js:
        return 0  # cheap gate: skip the tree-sitter parse for non-webpack JS assets
    seeded: list[dict[str, str]] = []
    try:
        chunk_urls = chunkenum.enumerate_chunk_urls(
            js.decode("utf-8", "replace"), max_urls=settings.crawl_max_assets
        )
        if not chunk_urls:
            return 0
        existing = {row.url for row in run_assets.list_for_run(tenant_id, run_id)}
        remaining = settings.crawl_max_assets - len(existing)
        if remaining <= 0:
            return 0
        try:
            for ref in chunk_urls:
                run_queries.raise_if_control_requested(tenant_id, run_id)  # REQ-A4
                if len(seeded) >= remaining:
                    break
                chunk_url = urljoin(asset_url, ref)
                if chunk_url == asset_url or chunk_url in existing:
                    continue  # self or already known -> no duplicate fetch
                if job_id:
                    progress.beat(
                        redis,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        job_id=job_id,
                        done=done,
                        total=total,
                    )
                host = (urlsplit(chunk_url).hostname or "").lower()
                if host:
                    _await_host_slot(
                        redis,
                        host,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        job_id=job_id,
                        settings=settings,
                    )
                try:
                    chunk_bytes = fetch_url(
                        chunk_url,
                        scope_hosts,
                        timeout_s=settings.fetch_timeout_seconds,
                        max_bytes=max_bytes,
                        allow_local=settings.allow_local_egress,
                    )
                except (egress.EgressBlocked, retry.FatalError, retry.RetryableError) as exc:
                    log.info("fetch.chunk_skipped", run_id=run_id, url=chunk_url, error=str(exc))
                    continue
                chunk_key = storage.put_blob(tenant_id, run_id, "input", chunk_bytes)
                seeded.append({"url": chunk_url, "input_ref": chunk_key})
                existing.add(chunk_url)
        finally:
            if seeded:  # persist fetched-so-far on EVERY exit (normal / interrupt / soft-miss)
                with tenant_session(tenant_id) as s:
                    run_assets.seed_captured(s, tenant_id=tenant_id, run_id=run_id, rows=seeded)
        return len(seeded)
    except retry.ControlInterrupt:
        raise  # REQ-A4: pause/cancel must propagate, never be eaten by the soft-miss guard below
    except Exception as exc:  # noqa: BLE001 — soft miss; enumeration must never fail the asset
        log.info("fetch.chunk_enum_skipped", run_id=run_id, url=asset_url, error=str(exc))
        return len(seeded)


def _authorized_engagement(
    tenant_id: str, run_id: str
) -> tuple[sessions_service.SessionView, int | None]:
    """The run's authorized engagement (REQ-P3) AND its per-run fetch-cap override
    (``run.max_fetch_bytes``; None = the global default), read in one pass."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        session_id = str(run.session_id) if run is not None else None
        run_cap = run.max_fetch_bytes if run is not None else None
    engagement = sessions_service.get_session(tenant_id, session_id)
    if engagement is None or not engagement.authorization_ack:
        raise retry.FatalError("session is not authorized for egress")
    return engagement, run_cap
