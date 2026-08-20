"""Out-of-band wrapper re-extract (REQ-C2 first clause) — spec §6.

Re-reads a terminal run's stored source blob(s) and records the endpoint findings
recognized under a set of taught wrapper rules, through the existing idempotent
outbox (REQ-A3). Records ONLY endpoints — no Kingfisher subprocess, no
`analyze.coverage` event (spec §2.6/§12 Blocker 1) — and never transitions run
state, mirroring `recon.spec.service.reclassify_run`. Each blob is read in its own
`tenant_session`, so a run invisible to the tenant (RLS) resolves to `None` (the
router maps that to 404). A vanished source blob maps to a clean
`SourceBlobMissing` (§12 Minor 9) rather than a raw 500.

`_extract_endpoints` is imported deliberately: the spec (§3/§6) names it as the
endpoints-only core re-extract calls directly, bypassing `_analyze_blob`'s
secrets + coverage.
"""

from __future__ import annotations

from collections.abc import Sequence

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import Finding, Run
from recon.domain import AssetStatus
from recon.findings import normalize
from recon.findings.analyze import (
    CrossModuleIndex,
    _extract_endpoints,
    _harvest_asset_exports,
    build_export_index,
)
from recon.findings.wrappers import WrapperRule
from recon.runs import assets as run_assets


class SourceBlobMissing(Exception):
    """A run's stored source blob is gone — re-extract cannot proceed (spec §12 Minor 9)."""


class StaleFindingIdentity(Exception):
    """The run's findings were hashed under an older finding-identity version, so an
    additive re-extract (ON CONFLICT on run_id+finding_hash) would write a current-version
    hash beside the old-version row for the same logical finding — a duplicate. New-runs-only
    rollout (normalize.FINDING_HASH_VERSION): re-run the target to re-extract under the
    current version instead of re-extracting in place."""


def reextract_run(tenant_id: str, run_id: str, wrappers: Sequence[WrapperRule]) -> int | None:
    """Re-extract `run_id` under `wrappers`; return the number of finding/occurrence
    rows newly written (0 when nothing is new — the outbox is idempotent), or `None`
    if the run is invisible to `tenant_id` (RLS) or does not exist.

    Run-scoped by design (spec §2.5/§12 Minor 5): re-reads only THIS run's blobs,
    not every sibling run in the session."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        input_ref = run.input_ref
        source_map_ref = run.source_map_ref
        # Refuse an in-place re-extract onto a run whose findings predate the current
        # identity version — the additive write would duplicate them (see
        # StaleFindingIdentity). A current-version finding's stored hash equals
        # finding_hash(type, value); an older one won't. The run is version-uniform
        # (analyzed once, and old runs never reach this write path), so one row decides.
        sample = session.execute(
            select(Finding.type, Finding.value, Finding.finding_hash)
            .where(Finding.run_id == str(run_id))
            .limit(1)
        ).first()
        if sample is not None and normalize.finding_hash(sample[0], sample[1]) != sample[2]:
            raise StaleFindingIdentity(str(run_id))

    rows = run_assets.list_for_run(tenant_id, run_id)
    written = 0
    try:
        if rows:  # multi-asset run: one blob per fetched asset (each may carry a capture map)
            # Same cross-module index the analyze stage built, so a re-extract of
            # `fetch(API_BASE + PATH)` / `fetch(r.t + r.M)` resolves to the IDENTICAL
            # endpoint the analyze pass wrote — never a contradictory ENDPOINT_UNRESOLVED
            # skeleton beside it (best-effort; a bad asset just yields nothing).
            cross_index = build_export_index(rows, source_map_origin="capture")
            for asset in rows:
                if asset.fetch_status != AssetStatus.OK.value or not asset.input_ref:
                    continue
                with tenant_session(tenant_id) as session:
                    # Thread the asset's source map + "capture" origin exactly as the
                    # analyze stage does (analyze.py `_analyze_assets`): a capture
                    # asset's original findings are attributed to the map-recovered
                    # path, so re-extract MUST recover the same paths or it would write
                    # the wrapper endpoint under `input.js` — a divergent finding_hash,
                    # i.e. a duplicate finding instead of an update (§12 Imp 4).
                    written += _reextract_blob(
                        session,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        input_ref=asset.input_ref,
                        source_map_ref=asset.source_map_ref,
                        source_map_origin="capture",
                        run_asset_id=asset.id,
                        asset_url=asset.url,
                        wrappers=wrappers,
                        cross_index=cross_index,
                    )
        elif input_ref:  # legacy single-blob run (with its own source map, if any)
            legacy_index = CrossModuleIndex()
            try:
                _harvest_asset_exports(input_ref, source_map_ref, None, "uploaded", legacy_index)
            except Exception:  # noqa: BLE001 - best-effort; no cross-module on failure
                legacy_index = CrossModuleIndex()
            with tenant_session(tenant_id) as session:
                written += _reextract_blob(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    input_ref=input_ref,
                    source_map_ref=source_map_ref,
                    run_asset_id=None,
                    asset_url=None,
                    wrappers=wrappers,
                    cross_index=legacy_index,
                )
    except ClientError as exc:  # storage.get_blob on a vanished blob (§12 Minor 9)
        raise SourceBlobMissing(str(exc)) from exc
    return written


def _reextract_blob(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    input_ref: str,
    source_map_ref: str | None,
    source_map_origin: str = "uploaded",
    run_asset_id: str | None,
    asset_url: str | None,
    wrappers: Sequence[WrapperRule],
    cross_index: CrossModuleIndex | None = None,
) -> int:
    raw = storage.get_blob(input_ref)
    source = raw.decode("utf-8", "replace")
    return _extract_endpoints(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        source=source,
        source_map_ref=source_map_ref,
        source_map_origin=source_map_origin,
        run_asset_id=run_asset_id,
        asset_url=asset_url,
        wrappers=wrappers,
        cross_index=cross_index,
    ).written
