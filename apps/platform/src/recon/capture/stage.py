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
from urllib.parse import urljoin, urlsplit

from redis import Redis

from recon import storage
from recon.capture import driver
from recon.config import Settings, clamp_fetch_bytes, get_settings
from recon.db.base import tenant_session
from recon.events.log import publish, record_event
from recon.fetch import egress, fetch
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
    max_fetch_bytes: int | None = None,
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

    # Per-run fetch-cap override (edit-&-re-run) passed down from discover_run (which
    # already loaded the run), clamped to the ceiling; bounds each captured script's
    # stored size just as it bounds a static fetch (REQ-Q5).
    cap = clamp_fetch_bytes(max_fetch_bytes, settings)
    try:
        result = driver.capture_scripts(
            seed,
            chrome_path=settings.system_chrome_path,
            nav_timeout_s=settings.capture_nav_timeout_seconds,
            idle_settle_s=settings.capture_idle_settle_seconds,
            session_budget_s=settings.crawl_duration_seconds + settings.crawl_kill_grace_seconds,
            heartbeat_interval_s=settings.crawl_heartbeat_interval_seconds,
            max_scripts=settings.capture_max_scripts,
            max_script_bytes=cap,
            max_requests=settings.capture_max_requests,
            interact=settings.capture_interact,
            max_scroll_steps=settings.capture_max_scroll_steps,
            max_clicks=settings.capture_max_clicks,
            max_routes=settings.capture_max_routes,
            on_progress=on_progress,
        )
    except driver.CaptureError as exc:
        # A launch/port/connect failure is environmental — worth a bounded retry
        # (the attempt cap still lands a persistently-broken browser in the DLQ).
        raise retry.RetryableError(f"capture browser failed: {exc}") from exc

    target_host = egress.host_of(seed)
    kept = _in_scope(
        result.scripts,
        target_host=target_host,
        scope_hosts=engagement.scope_hosts,
        allow_local=settings.allow_local_egress,
    )
    # REQ-C3: keep the observed XHR/fetch request URLs whose host is in scope (the SAME
    # name-only predicate as the script filter), for the correlate stage to resolve
    # endpoint hosts. These only LABEL statically-found endpoints — nothing is ever
    # fetched from them, so scope is never DERIVED from them (REQ-P2).
    kept_requests = _requests_in_scope(
        result.requests,
        target_host=target_host,
        scope_hosts=engagement.scope_hosts,
        allow_local=settings.allow_local_egress,
    )
    # Seeding stores one blob PER script (page + the whole worker/SW tree), so pass
    # the SAME heartbeat: N put_blob round-trips between the driver's last beat and the
    # manifest commit could otherwise lapse the job lease and let a peer double-launch
    # the browser (the very invariant the driver's beater protects) — and it keeps
    # pause/cancel responsive during seeding.
    seed_rows = _asset_rows(kept, tenant_id=tenant_id, run_id=run_id, heartbeat=on_progress)

    # Recover each captured script's EXTERNAL source map (parity with the static crawl's
    # CE2 fetch): guarded, DNS-pinned, soft-miss. Runs AFTER the pure blob-seeding pass
    # and BEFORE the atomic manifest — no DB session is open across the .map GETs. Gated
    # by the same kill-switch as the crawl; inline data: maps are left to analyze's
    # source-comment fallback, so only external refs are fetched here.
    maps_fetched = maps_missed = 0
    if settings.crawl_fetch_source_maps:
        maps_fetched, maps_missed = _augment_with_source_maps(
            kept,
            seed_rows,
            document_url=seed,
            scope_hosts=engagement.scope_hosts,
            tenant_id=tenant_id,
            run_id=run_id,
            settings=settings,
            max_bytes=cap,
            on_progress=on_progress,
        )

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
    # REQ-C3: persist the in-scope observed request URLs for the correlate stage — a blob
    # (not rows), parity with assets_ref, referenced from the same discover.assets event.
    requests_ref = storage.put_blob(
        tenant_id, run_id, "capture-requests", json.dumps(kept_requests).encode("utf-8")
    )
    # Atomic manifest: seed every fetched row + record the discover.assets event in
    # ONE transaction, then publish after commit (REQ-R2 commit-then-publish).
    with tenant_session(tenant_id) as session:
        assets.seed_captured(session, tenant_id=tenant_id, run_id=run_id, rows=seed_rows)
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="discover.assets",
            payload={
                "count": len(seed_rows),
                "assets_ref": assets_ref,
                "requests_ref": requests_ref,
                "status": status,
            },
        )
    publish(redis, event)
    log.info(
        "capture.done",
        run_id=run_id,
        count=len(seed_rows),
        observed_requests=len(kept_requests),
        status=status,
        nav_error=result.nav_error,
        # Provenance: which execution contexts the scripts came from (page vs the
        # worker / service-worker tree, C7/C8) — the point of slice 2.
        by_target_type=dict(Counter(s.target_type for s in kept)),
        # Source-map recovery: how many external .map files were fetched vs soft-missed
        # (blocked/oversized/malformed) — so the new egress is diagnosable (CLAUDE.md §5).
        source_maps_fetched=maps_fetched,
        source_maps_missed=maps_missed,
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


def _requests_in_scope(
    requests: list[dict],
    *,
    target_host: str,
    scope_hosts: list[str],
    allow_local: bool,
) -> list[dict]:
    """Keep observed requests served by an in-scope host — the SAME name-only predicate
    as the script filter (a subdomain of a scoped host is in scope, so a
    ``dashboard.x.com`` target scoped ``x.com`` keeps its ``api.x.com`` calls). A request
    URL is always absolute (the driver normalized it to ``scheme://host/path``), so unlike
    a script there is no anonymous/host-less case."""
    kept: list[dict] = []
    for req in requests:
        host = (urlsplit(req["url"]).hostname or "").lower()
        if host == target_host or egress.host_in_scope(host, scope_hosts, allow_local=allow_local):
            kept.append(req)
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
        # source_map_ref defaults None on EVERY row (uniform bulk-insert columns);
        # _augment_with_source_maps overwrites it for scripts with an external map.
        rows.append({"url": url, "input_ref": input_ref, "source_map_ref": None})
        if heartbeat is not None and len(rows) % _SEED_HEARTBEAT_EVERY == 0:
            heartbeat(len(rows))
    return rows


def _augment_with_source_maps(
    scripts: list[driver.CapturedScript],
    rows: list[dict],
    *,
    document_url: str,
    scope_hosts: list[str],
    tenant_id: str,
    run_id: str,
    settings: Settings,
    max_bytes: int,
    on_progress: Callable[[int], None],
) -> tuple[int, int]:
    """Fetch each captured script's EXTERNAL source map and link it on the matching row.

    ``scripts`` and ``rows`` are 1:1 and same-order (``_asset_rows`` maps every script to
    a row), so they zip. Only an external ``sourceMapURL`` is fetched: a script with no
    map, or an inline ``data:`` map, is skipped — analyze already recovers an inline map
    from the source's own ``//# sourceMappingURL=`` comment, so re-fetching it would be
    redundant. Returns ``(fetched, soft_missed)`` for the ``capture.done`` log.

    The membership check (``not url``/``startswith("data:")``) can't raise; the raise-prone
    URL resolution + GET lives inside the per-script soft-miss boundary
    (``_fetch_captured_source_map``), so a single crafted ``sourceMapURL`` can never abort
    the whole seeding pass."""
    fetched = missed = 0
    for script, row in zip(scripts, rows, strict=True):  # _asset_rows is 1:1 with scripts
        source_map_url = script.source_map_url
        if not source_map_url or source_map_url.startswith("data:"):
            continue
        # Resolve against the script's REAL url (or the document url for an anonymous /
        # eval'd script) — never the row's rewritten vm://<sha> placeholder.
        base = script.url or document_url
        ref = _fetch_captured_source_map(
            script,
            base=base,
            scope_hosts=scope_hosts,
            tenant_id=tenant_id,
            run_id=run_id,
            settings=settings,
            max_bytes=max_bytes,
            on_progress=on_progress,
        )
        if ref is not None:
            row["source_map_ref"] = ref
            fetched += 1
        else:
            missed += 1
    return fetched, missed


def _fetch_captured_source_map(
    script: driver.CapturedScript,
    *,
    base: str,
    scope_hosts: list[str],
    tenant_id: str,
    run_id: str,
    settings: Settings,
    max_bytes: int,
    on_progress: Callable[[int], None],
) -> str | None:
    """Guarded-fetch one captured script's external ``.map`` and store it, returning the
    blob key (or ``None`` on any soft miss). Uses the CDP-reported ``sourceMapURL``
    (authoritative — it also covers a ``SourceMap:`` response header and survives the
    driver's oversize-source truncation) rather than re-deriving it from a source comment.

    ``on_progress`` (a job-lease beat folded with the REQ-A4 pause/cancel check) fires
    BEFORE the outbound GET, so a cancel during seeding is observed per-map (not once per
    25 rows) and can propagate; it is deliberately OUTSIDE the try, so a genuine cancel is
    never swallowed as a soft miss. Everything after — the raise-prone ``urljoin`` and the
    ``fetch_url`` GET — is a NON-RAISING soft miss (a crafted/blocked/oversized/malformed
    map leaves ``source_map_ref`` null and analyze falls back to the minified bundle; the
    script's own blob is already stored and unaffected). No per-host politeness slot is
    taken: the browser already drove far more traffic at this host during capture, and the
    bounded, sequential ``.map`` GETs don't warrant the crawl's anti-hammer accounting —
    the load-bearing lease/cancel invariant is preserved by the pre-GET beat. Mirrors
    ``fetch._fetch_and_store_source_map`` (REQ-CE2) through the same ``fetch_url`` guard."""
    on_progress(0)
    try:
        map_url = urljoin(base, script.source_map_url or "")
        map_bytes = fetch.fetch_url(
            map_url,
            scope_hosts,
            timeout_s=settings.fetch_timeout_seconds,
            max_bytes=max_bytes,
            allow_local=settings.allow_local_egress,
        )
        return storage.put_blob(tenant_id, run_id, "source_map", map_bytes)
    except Exception as exc:  # noqa: BLE001 — soft miss; a bad map must never fail capture
        log.info(
            "capture.source_map_skipped",
            run_id=run_id,
            url=script.url or "",
            source_map_url=script.source_map_url,
            error=str(exc),
        )
        return None
