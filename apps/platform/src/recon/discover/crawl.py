# src/recon/discover/crawl.py
"""Discover stage — crawl the run's domain into an in-scope .js assets manifest.

Idempotent: returns without re-crawling if a discover.assets event already exists
(a headless crawl must not repeat on redelivery). The crawl SEED is gated through
the same egress guard (scope + public-IP) BEFORE katana launches, and every URL
katana emits is independently re-validated before it can enter the manifest — so
neither the seed nor a scope-escape in katana output can point the crawler at an
internal/out-of-scope address (REQ-P2 / SSRF). Residual: katana resolves the host
and loads subresources itself with no IP pin, so DNS-rebinding mid-crawl remains
an accepted residual risk, closed only by the deferred egress-proxy slice.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from redis import Redis

from recon import storage
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.discover import harness, katana, queries
from recon.events.log import publish, record_event
from recon.fetch import egress
from recon.observability import get_logger
from recon.queue import retry
from recon.runs import assets
from recon.sessions import service as sessions_service

log = get_logger("recon.discover")


def discover_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str) -> None:
    if queries.latest_assets_event(tenant_id, run_id) is not None:
        return  # already discovered (stage retry / redelivery)

    target, session_id, input_ref = _load_target(tenant_id, run_id)
    # An upload run carries a `target` only as a base-URL hint (REQ-C2), never as a
    # crawl seed — analyze reads the uploaded blob, so we must not crawl it.
    if input_ref is not None:
        return
    if not target:
        return  # a target-less crawl run — nothing to discover

    if not _is_bare_domain(target):
        return  # a single asset URL, not a domain crawl — legacy path handles it

    engagement = sessions_service.get_session(tenant_id, session_id)
    if engagement is None or not engagement.authorization_ack:
        raise retry.FatalError("session is not authorized for recon")
    # SSRF: gate the crawl SEED through the full egress guard (scope + DNS +
    # public-IP), not just host_in_scope, so an in-scope host that resolves to an
    # internal address, or an IP-literal / localhost / bad-scheme seed, is rejected
    # BEFORE katana (a raw subprocess with no IP check of its own) launches. Any
    # block is fatal: a bad seed never succeeds on retry, and a transient DNS
    # failure surfaces loudly in the DLQ instead of silently completing with 0
    # assets. (Katana still resolves the host itself, so rebinding mid-crawl stays
    # a residual risk — see the module docstring / the deferred egress-proxy slice.)
    settings = get_settings()
    try:
        egress.validate_target(
            _seed_url(target), engagement.scope_hosts, allow_local=settings.allow_local_egress
        )
    except egress.EgressBlocked as exc:
        raise retry.FatalError(f"crawl seed blocked by egress guard: {exc}") from exc

    argv = katana.build_argv(
        katana_bin=settings.katana_bin, domain=target,
        scope_hosts=engagement.scope_hosts, depth=settings.crawl_depth,
        crawl_duration_seconds=settings.crawl_duration_seconds,
        headless=settings.crawl_headless,
        system_chrome_path=settings.system_chrome_path,
        js_crawl=settings.crawl_js_crawl,
    )
    result = harness.run_crawl(
        redis, argv, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
        duration_seconds=settings.crawl_duration_seconds,
        kill_grace_seconds=settings.crawl_kill_grace_seconds,
        heartbeat_interval_seconds=settings.crawl_heartbeat_interval_seconds,
        max_output_bytes=settings.crawl_max_output_bytes,
    )

    in_scope = _revalidate(
        katana.parse_assets(result.stdout), engagement.scope_hosts,
        allow_local=settings.allow_local_egress,
    )
    capped = len(in_scope) > settings.crawl_max_assets
    kept = in_scope[: settings.crawl_max_assets]
    status = "timeout" if result.timed_out else ("capped" if capped else "ok")

    manifest = {
        "domain": target, "status": status,
        "assets": [{"url": u, "source": "katana"} for u in kept],
    }
    assets_ref = storage.put_blob(
        tenant_id, run_id, "assets", json.dumps(manifest).encode("utf-8")
    )
    with tenant_session(tenant_id) as session:
        assets.seed_pending(
            session, tenant_id=tenant_id, run_id=run_id, urls=kept
        )
        event = record_event(
            session, tenant_id=tenant_id, run_id=run_id,
            event_type="discover.assets",
            payload={"count": len(kept), "assets_ref": assets_ref, "status": status},
        )
    publish(redis, event)
    log.info("discover.done", run_id=run_id, count=len(kept), status=status)


def _revalidate(
    urls: list[str], scope_hosts: list[str], *, allow_local: bool = False
) -> list[str]:
    kept: list[str] = []
    for url in urls:
        try:
            egress.validate_target(url, scope_hosts, allow_local=allow_local)
        except egress.EgressBlocked:
            continue
        kept.append(url)
    return kept


def _load_target(
    tenant_id: str, run_id: str
) -> tuple[str | None, str | None, str | None]:
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None, None, None
        return run.target, str(run.session_id), run.input_ref


def _is_bare_domain(target: str) -> bool:
    """A crawl target must be a bare host (no path) — a target with a path is a
    single asset URL and stays on the legacy single-asset path (Slice Y backward
    compat; also closes the Slice X 'crawls any in-scope target' latent guard)."""
    t = target if "://" in target else f"https://{target}"
    path = urlsplit(t).path
    return path in ("", "/")


def _seed_url(target: str) -> str:
    """A bare-domain crawl target as a fetchable URL (default https) so the egress
    guard's scheme + host checks apply to the seed."""
    return target if "://" in target else f"https://{target}"
