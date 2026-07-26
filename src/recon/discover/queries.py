"""Read side for discovery: the latest assets event + its manifest blob."""

from __future__ import annotations

import json

from sqlalchemy import select

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import RunEvent


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
