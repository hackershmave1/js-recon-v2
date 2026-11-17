"""Analyze's best-effort per-host tech-detection fingerprint pass (Task 8).

Runs AFTER per-asset analysis (Vespasian/Kingfisher): loads and merges the run's
``fingerprint-signal`` blobs (Tasks 6/7, plus one per extension upload batch, D77 —
allowlisted headers/scripts/meta/cookies per host), matches it against the vendored dataset via ``techdetect.detect``
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
    """Detect per-host technologies from the run's (merged) fingerprint signal and upsert
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
    """The run's fingerprint signal as ``{host: HostSignal}``: the MERGE of every
    ``fingerprint.signal`` event's blob, oldest first, so a later batch's header value
    wins. ``{}`` if none is visible.

    Merged, not latest-only (DEBT D77): the extension ingest writes one signal per
    uploaded batch, and a redelivered crawl fetch writes a second, partial one — a
    latest-only read kept just the last batch's hosts. A retried batch repeats a
    content-addressed ref, read once. ``_MAX_SIGNAL_BYTES`` bounds the TOTAL loaded: a
    blob that would exceed what is left is dropped (logged) and the rest still merge."""
    blobs: list[dict[str, Any]] = []
    loaded = 0
    for ref in dict.fromkeys(_signal_refs(tenant_id, run_id)):
        raw = storage.get_blob(ref)
        if loaded + len(raw) > _MAX_SIGNAL_BYTES:
            log.warning("analyze.fingerprint_signal_oversized", run_id=run_id, bytes=len(raw))
            continue
        loaded += len(raw)
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            blobs.append(data)
    return _merge_signals(blobs)


def _signal_refs(tenant_id: str, run_id: str) -> list[str]:
    """Every ``fingerprint.signal`` event's blob ref for the run, oldest first."""
    with tenant_session(tenant_id) as session:
        payloads = session.scalars(
            select(RunEvent.payload)
            .where(RunEvent.run_id == str(run_id), RunEvent.type == "fingerprint.signal")
            .order_by(RunEvent.id)
        ).all()
    return [p["signal_ref"] for p in payloads if isinstance(p, dict) and p.get("signal_ref")]


def _merge_signals(blobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fold host-keyed signals into one: hosts union, ``headers`` later-wins, the list
    fields (scripts/meta/cookies) order-preserving unions. A malformed host entry or
    field is skipped — the blob is our own, but the extension's half of it came from an
    untrusted client."""
    merged: dict[str, dict[str, Any]] = {}
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for host, host_signal in blob.items():
            if not isinstance(host_signal, dict):
                continue
            entry = merged.setdefault(
                host, {"headers": {}, "scripts": [], "meta": [], "cookies": []}
            )
            headers = host_signal.get("headers")
            if isinstance(headers, dict):
                entry["headers"].update(headers)
            for field in ("scripts", "meta", "cookies"):
                values = host_signal.get(field)
                if isinstance(values, list):
                    entry[field].extend(v for v in values if v not in entry[field])
    return merged


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
        # Slice each blob to the host's REMAINING budget so the total JS fed to the
        # scripts-field matcher is a hard <= _MAX_JS_BYTES_PER_HOST per host. Counting
        # full blob length while appending a per-asset 2 MB slice (the earlier form)
        # let one asset straddling the threshold push the effective total to ~2x — a
        # real cost now that the full dataset carries ~800 scripts-surface patterns.
        remaining = _MAX_JS_BYTES_PER_HOST - budget.get(host, 0)
        if host not in hosts or remaining <= 0:
            continue
        raw = storage.get_blob(asset.input_ref)[:remaining]
        budget[host] = budget.get(host, 0) + len(raw)
        by_host.setdefault(host, []).append(raw.decode("utf-8", "replace"))
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
