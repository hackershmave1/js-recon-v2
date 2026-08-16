"""Analyze's best-effort per-host tech-detection fingerprint pass (Task 8).

Runs AFTER per-asset analysis (Vespasian/Kingfisher): loads the run's ONE
``fingerprint-signal`` blob (Tasks 6/7 — allowlisted headers/scripts/meta/cookies
per host), matches it against the vendored dataset via ``techdetect.detect``
(Tasks 2-5), and upserts ``run_technology`` per ``(run_id, host, name)`` (T3).

This module raises normally — a ``ControlInterrupt`` (REQ-A4) from a mid-pass
pause/cancel, a dataset load failure (T7's runtime defense-in-depth; a load-time
test guarantees presence in a correct build), or any other error. The caller
(``recon.findings.analyze.analyze_run``) wraps the call to :func:`run_fingerprint_pass`
so the pass can never fail the run (T2), re-raising only the control interrupt.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from redis import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import RunEvent, RunTechnology
from recon.events.log import publish, record_event
from recon.findings import techdetect
from recon.observability import get_logger
from recon.progress import heartbeat as progress
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries

log = get_logger("recon.findings.techdetect_pass")

_MAX_SIGNAL_BYTES = 2_000_000  # cap on the loaded signal blob (best-effort, bounded)
_MAX_JS_BYTES_PER_HOST = 2_000_000  # cap on JS fed to the scripts-field matcher, per host


def run_fingerprint_pass(redis: Redis, *, tenant_id: str, run_id: str, job_id: str | None) -> None:
    """Detect per-host technologies from the run's fingerprint-signal blob and upsert
    ``run_technology`` (T3). Heartbeats + checks control between hosts (REQ-A4). A
    run with no fingerprint-signal event (no crawl/capture producer ran, or a legacy
    upload run) is a silent no-op — there is nothing to detect."""
    signal = _load_fingerprint_signal(tenant_id, run_id)
    if not signal:
        return
    js_by_host = _js_texts_by_host(tenant_id, run_id, hosts=set(signal))
    host_counts: dict[str, int] = {}
    with tenant_session(tenant_id) as session:
        for host, host_signal in signal.items():
            run_queries.raise_if_control_requested(tenant_id, run_id)  # REQ-A4 (propagates)
            if job_id:
                progress.beat(
                    redis,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    job_id=job_id,
                    done=0,
                    total=0,
                    emit_event=False,
                )
            detections = techdetect.detect(host, host_signal, js_by_host.get(host, []))
            for detection in detections:
                _upsert_technology(session, tenant_id, run_id, host, detection)
            host_counts[host] = len(detections)
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="analyze.technologies",
            payload={
                "hosts": host_counts,
                "dataset_commit": techdetect.dataset_commit(),
                "skipped_patterns": techdetect.skipped_pattern_count(),
            },
        )
    publish(redis, event)  # commit-then-publish (REQ-R2)
    log.info("analyze.technologies", run_id=run_id, hosts=host_counts)


def _load_fingerprint_signal(tenant_id: str, run_id: str) -> dict[str, Any]:
    """The latest ``fingerprint.signal`` blob for the run as ``{host: HostSignal}``,
    size-capped; ``{}`` if absent/oversized/invisible."""
    with tenant_session(tenant_id) as session:
        payload = session.scalar(
            select(RunEvent.payload)
            .where(RunEvent.run_id == str(run_id), RunEvent.type == "fingerprint.signal")
            .order_by(RunEvent.id.desc())
        )
    if not payload:
        return {}
    ref = payload.get("signal_ref")
    if not ref:
        return {}
    raw = storage.get_blob(ref)
    if len(raw) > _MAX_SIGNAL_BYTES:
        log.warning("analyze.fingerprint_signal_oversized", run_id=run_id, bytes=len(raw))
        return {}
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _js_texts_by_host(tenant_id: str, run_id: str, *, hosts: set[str]) -> dict[str, list[str]]:
    """Stored JS bytes per host for the ``scripts``-field match, capped per host.

    Reads each asset's ALREADY-STORED blob by its ``input_ref`` key (the same one
    Vespasian/Kingfisher analyzed) — never a new network fetch of the target."""
    by_host: dict[str, list[str]] = {}
    budget: dict[str, int] = {}
    for asset in run_assets.list_for_run(tenant_id, run_id):
        if not asset.input_ref:
            continue
        host = (urlsplit(asset.url).hostname or "").lower()
        if host not in hosts or budget.get(host, 0) >= _MAX_JS_BYTES_PER_HOST:
            continue
        raw = storage.get_blob(asset.input_ref)
        budget[host] = budget.get(host, 0) + len(raw)
        by_host.setdefault(host, []).append(raw[:_MAX_JS_BYTES_PER_HOST].decode("utf-8", "replace"))
    return by_host


def _upsert_technology(
    session: Session, tenant_id: str, run_id: str, host: str, detection: techdetect.Detection
) -> None:
    """Upsert one detection on ``(run_id, host, name)`` — redelivery-safe (T3)."""
    stmt = (
        pg_insert(RunTechnology)
        .values(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            host=host,
            name=detection.name,
            categories=detection.categories,
            version=detection.version,
            confidence=detection.confidence,
            evidence=detection.evidence,
        )
        .on_conflict_do_update(
            index_elements=["run_id", "host", "name"],
            set_={
                "categories": detection.categories,
                "version": detection.version,
                "confidence": detection.confidence,
                "evidence": detection.evidence,
            },
        )
    )
    session.execute(stmt)
