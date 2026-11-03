"""The ``discover.assets`` record: the one place that knows its manifest blob, event payload and
status vocabulary, for writing (crawl, browser capture, extension ingest) and reading
(coordinator finalize, correlate, the assets read side, discover's redelivery short-circuit).

Writing is two steps because two invariants pull apart. ``store_manifest`` does the S3 writes and
runs OUTSIDE any DB transaction — the extension seal holds a run-row lock that must never span an
S3 round-trip (api/capture_router.analyze_start). ``record`` then appends the event INSIDE the
caller's transaction, beside whatever rows or Job the caller commits with it; it never publishes —
the caller publishes after commit (REQ-R2).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import RunEvent
from recon.domain import DiscoveryStatus
from recon.events.log import RecordedEvent, record_event

EVENT_TYPE = "discover.assets"


@dataclass(frozen=True)
class Discovery:
    """A run's discovered asset set. ``status`` is ``None`` for a status this code doesn't know,
    which reads as incomplete (fail closed)."""

    status: DiscoveryStatus | None
    count: int
    assets_ref: str | None
    # The runtime request observations blob (capture paths only). None means none were stored,
    # and correlate no-ops; an empty list stored is still a blob, and correlate reports 0 seen.
    requests_ref: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status is DiscoveryStatus.OK


def store_manifest(
    *,
    tenant_id: str,
    run_id: str,
    domain: str,
    status: DiscoveryStatus,
    asset_urls: Sequence[str],
    source: str,
    observed_requests: list[dict] | None = None,
) -> Discovery:
    """Write the manifest blob (and the observations blob, when given) — S3 only, no DB."""
    manifest = {
        "domain": domain,
        "status": status.value,
        "assets": [{"url": url, "source": source} for url in asset_urls],
    }
    assets_ref = storage.put_blob(tenant_id, run_id, "assets", json.dumps(manifest).encode("utf-8"))
    requests_ref = None
    if observed_requests is not None:
        requests_ref = storage.put_blob(
            tenant_id, run_id, "capture-requests", json.dumps(observed_requests).encode("utf-8")
        )
    return Discovery(
        status=status, count=len(asset_urls), assets_ref=assets_ref, requests_ref=requests_ref
    )


def record(session: Session, *, tenant_id: str, run_id: str, found: Discovery) -> RecordedEvent:
    """Append the ``discover.assets`` event in the caller's open transaction."""
    status = found.status.value if found.status else None
    payload: dict = {"count": found.count, "assets_ref": found.assets_ref, "status": status}
    if found.requests_ref:
        payload["requests_ref"] = found.requests_ref
    return record_event(
        session, tenant_id=tenant_id, run_id=run_id, event_type=EVENT_TYPE, payload=payload
    )


def read_latest(tenant_id: str, run_id: str) -> Discovery | None:
    """The run's latest discovery, or ``None`` when it has none (a legacy single-asset or
    upload run — the coordinator finalizes those DONE, unlike an incomplete discovery)."""
    with tenant_session(tenant_id) as session:
        row = session.execute(
            select(RunEvent.payload)
            .where(RunEvent.run_id == run_id, RunEvent.type == EVENT_TYPE)
            .order_by(RunEvent.id.desc())
            .limit(1)
        ).first()
    if row is None:
        return None
    payload = row[0]
    try:
        status: DiscoveryStatus | None = DiscoveryStatus(payload.get("status"))
    except ValueError:
        status = None
    return Discovery(
        status=status,
        count=int(payload.get("count") or 0),
        assets_ref=payload.get("assets_ref"),
        requests_ref=payload.get("requests_ref"),
    )


def read_manifest(found: Discovery | None) -> dict | None:
    """The manifest blob a discovery points at, or ``None``."""
    if found is None or not found.assets_ref:
        return None
    return json.loads(storage.get_blob(found.assets_ref))
