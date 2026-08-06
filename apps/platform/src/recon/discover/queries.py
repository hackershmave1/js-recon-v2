"""Read side for discovery: the latest assets event + its manifest blob."""

from __future__ import annotations

import json

from sqlalchemy import select

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import RunEvent
from recon.runs import assets as run_assets


def latest_assets_event(tenant_id: str, run_id: str) -> dict | None:
    with tenant_session(tenant_id) as session:
        row = session.execute(
            select(RunEvent.payload)
            .where(RunEvent.run_id == run_id, RunEvent.type == "discover.assets")
            .order_by(RunEvent.id.desc())
            .limit(1)
        ).first()
    return dict(row[0]) if row is not None else None


def get_assets_manifest(tenant_id: str, run_id: str) -> dict | None:
    payload = latest_assets_event(tenant_id, run_id)
    if payload is None or not payload.get("assets_ref"):
        return None
    return json.loads(storage.get_blob(payload["assets_ref"]))


def get_assets_with_status(tenant_id: str, run_id: str) -> dict | None:
    """Merge per-asset fetch/analyze status onto the discovery manifest.

    Returns the manifest with each asset enriched by fetch_status and
    analyze_status from the run_assets table (missing rows default to
    "pending").
    """
    manifest = get_assets_manifest(tenant_id, run_id)
    if manifest is None:
        return None
    status_by_url = {a.url: a for a in run_assets.list_for_run(tenant_id, run_id)}
    for entry in manifest.get("assets", []):
        url = entry.get("url")
        row = status_by_url.get(url) if url else None
        entry["fetch_status"] = row.fetch_status if row else "pending"
        entry["analyze_status"] = row.analyze_status if row else "pending"
        # Surface WHY a fetch/analyze failed so the UI can explain a blocked crawl
        # (e.g. a Cloudflare 403) rather than showing an empty result.
        entry["fetch_error"] = row.fetch_error if row else None
        entry["analyze_error"] = row.analyze_error if row else None
    return manifest
