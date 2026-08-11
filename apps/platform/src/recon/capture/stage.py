"""Runtime-capture discover stage — the ``crawl_mode="capture"`` alternative to the
static katana crawl. Drives headless Chromium (``recon.capture.driver``) to capture
EXECUTED scripts, then writes them as the SAME asset contract the static crawl and
the extension capture-ingest path produce: one ``run_asset`` row per script (blob
kind ``"input"``, ``fetch_status=ok``) plus a ``discover.assets`` event. FETCH then
no-ops on the already-ok rows and ANALYZE runs the engines — unchanged.

SSRF posture (load-bearing): the seed URL is validated through the full egress guard
(scope + public-IP) BEFORE the browser launches, and every captured script's URL is
re-validated against scope before it is stored (out-of-scope third-party scripts are
dropped — a policy decision, parity with the static crawl's re-validation). The
browser itself still resolves the host and loads subresources with no per-hop IP pin
— the SAME residual as the opt-in headless katana crawl (see ``recon.discover.crawl``
docstring), and a widening vs the default static crawl, which is why capture is
DEFAULT-OFF (``RECON_ENABLE_CAPTURE_MODE``). OS/network egress isolation is the
deferred egress-proxy slice.

Durability: capture holds every byte in hand, so all blobs are stored first
(content-addressed, idempotent), then every row is seeded ``fetch_ok`` AND the
``discover.assets`` event is recorded in ONE transaction (atomic manifest), and only
then published. A crash before that commit leaves nothing committed (a clean
re-capture on redelivery); a crash after it means ``discover_run`` short-circuits on
the existing event and never re-launches the browser.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from urllib.parse import urlsplit

from redis import Redis

from recon import storage
from recon.capture import driver
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.events.log import publish, record_event
from recon.fetch import egress
from recon.observability import get_logger
from recon.progress import heartbeat as progress
from recon.queue import retry
from recon.runs import assets
from recon.runs import queries as run_queries
from recon.sessions import service as sessions_service

log = get_logger("recon.capture")

# Renew the job lease every N seeded blobs (see _asset_rows). Small enough that N
# put_blob round-trips can't approach the 30s stall threshold on real S3 latency.
_SEED_HEARTBEAT_EVERY = 25


def capture_run(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    job_id: str,
    target: str,
    session_id: str,
) -> None:
    """Capture the run's target with a headless-Chromium CDP session and seed its
    executed scripts as fetched assets. Called from ``discover_run`` when
    ``run.crawl_mode == "capture"`` (which also owns the idempotency short-circuit)."""
    settings = get_settings()
    # Kill-switch (defense-in-depth; the API also gates it). Fatal, not retryable —
    # a disabled feature never becomes enabled on retry.
    if not settings.enable_capture_mode:
        raise retry.FatalError("runtime capture mode is disabled")

    # REQ-P3: scope membership is NOT authorization — capture drives a real browser
    # at a live target, so require the session's explicit egress ack before launch.
    engagement = sessions_service.get_session(tenant_id, session_id)
    if engagement is None or not engagement.authorization_ack:
        raise retry.FatalError("session is not authorized for recon")

    # SSRF: gate the seed URL through the full egress guard (scope + public-IP)
    # BEFORE the browser launches, exactly as the static crawl gates its seed.
    seed = _navigable_url(target)
    try:
        egress.validate_target(
            seed, engagement.scope_hosts, allow_local=settings.allow_local_egress
        )
    except egress.EgressBlocked as exc:
        raise retry.FatalError(f"capture seed blocked by egress guard: {exc}") from exc

    def on_progress(n_scripts: int) -> None:
        # One callback folds the two single-thread invariants the driver must honor
        # while it blocks: cooperative pause/cancel (REQ-A4; raises ControlInterrupt,
        # handled by the worker) checked FIRST, then a job-lease heartbeat so no peer
        # reclaims and double-launches a browser. Called at most once per heartbeat
        # interval by the driver.
        run_queries.raise_if_control_requested(tenant_id, run_id)
        progress.beat(
            redis,
            tenant_id=tenant_id,
            run_id=run_id,
            job_id=job_id,
            done=n_scripts,
            total=0,
            emit_event=False,
        )

    try:
        result = driver.capture_scripts(
            seed,
            chrome_path=settings.system_chrome_path,
            nav_timeout_s=settings.capture_nav_timeout_seconds,
            idle_settle_s=settings.capture_idle_settle_seconds,
            session_budget_s=settings.crawl_duration_seconds + settings.crawl_kill_grace_seconds,
            heartbeat_interval_s=settings.crawl_heartbeat_interval_seconds,
            max_scripts=settings.capture_max_scripts,
            max_script_bytes=settings.max_fetch_bytes,
            on_progress=on_progress,
        )
    except driver.CaptureError as exc:
        # A launch/port/connect failure is environmental — worth a bounded retry
        # (the attempt cap still lands a persistently-broken browser in the DLQ).
        raise retry.RetryableError(f"capture browser failed: {exc}") from exc

    kept = _in_scope(
        result.scripts,
        target_host=egress.host_of(seed),
        scope_hosts=engagement.scope_hosts,
        allow_local=settings.allow_local_egress,
    )
    # Seeding stores one blob PER script (page + the whole worker/SW tree), so pass
    # the SAME heartbeat: N put_blob round-trips between the driver's last beat and the
    # manifest commit could otherwise lapse the job lease and let a peer double-launch
    # the browser (the very invariant the driver's beater protects) — and it keeps
    # pause/cancel responsive during seeding.
    seed_rows = _asset_rows(kept, tenant_id=tenant_id, run_id=run_id, heartbeat=on_progress)

    # A hard navigation failure (bot-wall / TLS / ERR_*) is recorded as "blocked"
    # (→ PARTIAL in coordinator finalize), NOT a false "ok"/DONE with zero scripts —
    # capture mode exists to defeat such walls, so it must not silently hide them.
    status = "blocked" if result.nav_error else "ok"
    manifest = {
        "domain": target,
        "status": status,
        "assets": [{"url": r["url"], "source": "capture"} for r in seed_rows],
    }
    assets_ref = storage.put_blob(tenant_id, run_id, "assets", json.dumps(manifest).encode("utf-8"))
    # Atomic manifest: seed every fetched row + record the discover.assets event in
    # ONE transaction, then publish after commit (REQ-R2 commit-then-publish).
    with tenant_session(tenant_id) as session:
        assets.seed_captured(session, tenant_id=tenant_id, run_id=run_id, rows=seed_rows)
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="discover.assets",
            payload={"count": len(seed_rows), "assets_ref": assets_ref, "status": status},
        )
    publish(redis, event)
    log.info(
        "capture.done",
        run_id=run_id,
        count=len(seed_rows),
        status=status,
        nav_error=result.nav_error,
        # Provenance: which execution contexts the scripts came from (page vs the
        # worker / service-worker tree, C7/C8) — the point of slice 2.
        by_target_type=dict(Counter(s.target_type for s in kept)),
    )


def _navigable_url(target: str) -> str:
    """A bare-domain capture target becomes ``https://<host>``; a full URL (with a
    path) is navigated as-is (capture opens the exact page, unlike the bare-domain
    crawl seed)."""
    return target if "://" in target else f"https://{target}"


def _in_scope(
    scripts: list[driver.CapturedScript],
    *,
    target_host: str,
    scope_hosts: list[str],
    allow_local: bool,
) -> list[driver.CapturedScript]:
    """Drop scripts served by an out-of-scope host (third-party CDNs) — SCOPE-MEMBERSHIP
    parity with the static crawl (name-only ``host_in_scope``, not the full
    ``validate_target`` DNS/IP guard: nothing is fetched from a stored capture, so an
    IP re-check would be pointless and could wrongly drop an in-scope script on a
    transient DNS blip). An anonymous/inline script (no URL, or the document URL) is
    attributed to the target host, so it is kept."""
    kept: list[driver.CapturedScript] = []
    for script in scripts:
        host = (urlsplit(script.url).hostname or "").lower() if script.url else target_host
        if (
            not host
            or host == target_host
            or egress.host_in_scope(host, scope_hosts, allow_local=allow_local)
        ):
            kept.append(script)
    return kept


def _asset_rows(
    scripts: list[driver.CapturedScript],
    *,
    tenant_id: str,
    run_id: str,
    heartbeat: Callable[[int], None] | None = None,
) -> list[dict]:
    """Map captured scripts to unique, content-stable ``run_asset`` rows.

    An external ``<script src>`` seen once keeps its real URL. Scripts that share a
    URL (multiple inline blocks report the document URL) or have none (anonymous
    ``eval``/injected code) get a content-addressed URL derived from the source
    SHA-256 — unique (the driver already deduped identical content) and STABLE across
    a re-capture, so redelivery never produces ordinal-dependent duplicates.

    ``heartbeat(n_done)`` (if given) is called every ``_SEED_HEARTBEAT_EVERY`` blobs so
    the job lease is renewed and pause/cancel observed across a large seeding pass; it
    may raise (cancel) — the blobs already stored are idempotent and nothing is
    committed until the caller's manifest transaction."""
    counts = Counter(s.url for s in scripts if s.url)
    rows: list[dict] = []
    for script in scripts:
        if script.url and counts[script.url] == 1:
            url = script.url
        elif script.url:
            url = f"{script.url}#sha256={script.sha256}"
        else:
            url = f"vm://{script.sha256}"
        input_ref = storage.put_blob(tenant_id, run_id, "input", script.source)
        rows.append({"url": url, "input_ref": input_ref})
        if heartbeat is not None and len(rows) % _SEED_HEARTBEAT_EVERY == 0:
            heartbeat(len(rows))
    return rows
