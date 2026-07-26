# src/recon/discover/crawl.py
"""Discover stage — crawl the run's domain into an in-scope .js assets manifest.

Idempotent: returns without re-crawling if a discover.assets event already exists
(a headless crawl must not repeat on redelivery). Every URL katana emits is
independently re-validated through the fetch stage's egress guard before it can
enter the manifest, so a scope-escape in katana output can never surface an
internal/out-of-scope URL (REQ-P2 / SSRF). The crawl's own subresource loads are
NOT guarded — accepted residual risk documented in the design spec.
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
from recon.sessions import service as sessions_service

log = get_logger("recon.discover")


def discover_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str) -> None:
    if queries.latest_assets_event(tenant_id, run_id) is not None:
        return  # already discovered (stage retry / redelivery)

    target, session_id = _load_target(tenant_id, run_id)
    if not target:
        return  # nothing to crawl (e.g. an upload run with no domain target)

    engagement = sessions_service.get_session(tenant_id, session_id)
    if engagement is None or not engagement.authorization_ack:
        raise retry.FatalError("session is not authorized for recon")
    if not egress.host_in_scope(_host(target), engagement.scope_hosts):
        raise retry.FatalError(f"crawl target not in engagement scope: {target}")

    settings = get_settings()
    argv = katana.build_argv(
        katana_bin=settings.katana_bin, domain=target,
        scope_hosts=engagement.scope_hosts, depth=settings.crawl_depth,
        crawl_duration_seconds=settings.crawl_duration_seconds,
        headless=settings.crawl_headless,
    )
    result = harness.run_crawl(
        redis, argv, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
        duration_seconds=settings.crawl_duration_seconds,
        kill_grace_seconds=settings.crawl_kill_grace_seconds,
        heartbeat_interval_seconds=settings.crawl_heartbeat_interval_seconds,
        max_output_bytes=settings.crawl_max_output_bytes,
    )

    in_scope = _revalidate(katana.parse_assets(result.stdout), engagement.scope_hosts)
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
        event = record_event(
            session, tenant_id=tenant_id, run_id=run_id,
            event_type="discover.assets",
            payload={"count": len(kept), "assets_ref": assets_ref, "status": status},
        )
    publish(redis, event)
    log.info("discover.done", run_id=run_id, count=len(kept), status=status)


def _revalidate(urls: list[str], scope_hosts: list[str]) -> list[str]:
    kept: list[str] = []
    for url in urls:
        try:
            egress.validate_target(url, scope_hosts)
        except egress.EgressBlocked:
            continue
        kept.append(url)
    return kept


def _load_target(tenant_id: str, run_id: str) -> tuple[str | None, str | None]:
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None, None
        return run.target, str(run.session_id)


def _host(target: str) -> str:
    t = target if "://" in target else f"https://{target}"
    return urlsplit(t).hostname or ""
