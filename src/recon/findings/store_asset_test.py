"""Slice Y: the asset dimension keeps the same finding's sightings distinct per asset."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import normalize
from recon.findings.store import Occurrence, record_finding
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _run_with_two_assets(tenant, session_id):
    with tenant_session(tenant) as s:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        s.add(run)
        s.flush()
        a1 = models.RunAsset(tenant_id=tenant, run_id=run.id, url="https://acme.io/a.js")
        a2 = models.RunAsset(tenant_id=tenant, run_id=run.id, url="https://acme.io/b.js")
        s.add_all([a1, a2])
        s.flush()
        return str(run.id), str(a1.id), str(a2.id)


def test_same_endpoint_two_assets_one_finding_two_occurrences(authorized_session):
    tenant, session_id = authorized_session
    run_id, a1, a2 = _run_with_two_assets(tenant, session_id)
    ep = normalize.normalize_endpoint("GET", "https://api.acme.io/users/1")
    # Identical path + offsets in both assets — only the asset dimension keeps them apart.
    common = dict(host=ep.host, raw_url="/users/1", source_path="input.js",
                  offset_start=5, offset_end=9)
    with tenant_session(tenant) as s:
        occ1 = Occurrence(run_asset_id=a1, asset_url="https://acme.io/a.js", **common)
        r1 = record_finding(s, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
                            value=ep.value, path="input.js", occurrence=occ1)
        occ2 = Occurrence(run_asset_id=a2, asset_url="https://acme.io/b.js", **common)
        r2 = record_finding(s, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
                            value=ep.value, path="input.js", occurrence=occ2)
    assert r1.finding_hash == r2.finding_hash
    assert r1.finding_created and not r2.finding_created  # one finding
    assert r1.occurrence_created and r2.occurrence_created  # two sightings
    with tenant_session(tenant) as s:
        assert s.execute(select(func.count()).select_from(models.Finding)
                         .where(models.Finding.run_id == run_id)).scalar() == 1
        occs = s.execute(
            select(models.FindingOccurrence).where(
                models.FindingOccurrence.finding_id == r1.finding_id
            )
        ).scalars().all()
        assert {str(o.run_asset_id) for o in occs} == {a1, a2}
