"""Read side for discovery: the run's manifest, with each asset's fetch/analyze status."""

from __future__ import annotations

from recon.discover import manifest as discovery
from recon.runs import assets as run_assets


def get_assets_manifest(tenant_id: str, run_id: str) -> dict | None:
    return discovery.read_manifest(discovery.read_latest(tenant_id, run_id))


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
