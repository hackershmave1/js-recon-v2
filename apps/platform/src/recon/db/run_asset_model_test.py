import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _make_run(tenant: str, session_id: str) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        session.add(run)
        session.flush()
        return str(run.id)


def test_run_asset_is_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("asset-a")
    tenant_b = sessions_service.create_tenant("asset-b")
    sv = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant_a, sv.id)
    with tenant_session(tenant_a) as session:
        session.add(
            models.RunAsset(
                tenant_id=tenant_a,
                run_id=run_id,
                url="https://acme.io/app.js",
            )
        )
    with tenant_session(tenant_a) as session:
        row = session.query(models.RunAsset).one()
        assert row.fetch_status == "pending" and row.analyze_status == "pending"
    with tenant_session(tenant_b) as session:
        assert session.query(models.RunAsset).count() == 0


def test_occurrence_has_run_asset_id_column():
    # The Slice Y column exists and defaults NULL (legacy occurrences are unaffected).
    tenant = sessions_service.create_tenant("occ-col")
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant, sv.id)
    with tenant_session(tenant) as session:
        finding = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="a" * 64,
            type="endpoint",
            value="GET /x",
            path="input.js",
        )
        session.add(finding)
        session.flush()
        occ = models.FindingOccurrence(
            tenant_id=tenant,
            finding_id=finding.id,
            occurrence_hash="b" * 64,
        )
        session.add(occ)
        session.flush()
        assert occ.run_asset_id is None


def test_deleting_run_asset_sets_occurrence_run_asset_id_null():
    # Slice Y reveal routing relies on the FK's ON DELETE SET NULL actually firing
    # at the DB level, not just on the column existing (see test above) — a bare
    # `uuid` column with no REFERENCES would leave this stale/dangling instead.
    tenant = sessions_service.create_tenant("asset-cascade")
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _make_run(tenant, sv.id)
    with tenant_session(tenant) as session:
        asset = models.RunAsset(
            tenant_id=tenant,
            run_id=run_id,
            url="https://acme.io/app.js",
        )
        session.add(asset)
        session.flush()
        asset_id = str(asset.id)

        finding = models.Finding(
            tenant_id=tenant,
            run_id=run_id,
            finding_hash="c" * 64,
            type="endpoint",
            value="GET /y",
            path="app.js",
        )
        session.add(finding)
        session.flush()
        occ = models.FindingOccurrence(
            tenant_id=tenant,
            finding_id=finding.id,
            occurrence_hash="d" * 64,
            run_asset_id=asset_id,
        )
        session.add(occ)
        session.flush()
        occ_id = str(occ.id)

    with tenant_session(tenant) as session:
        session.delete(session.get(models.RunAsset, asset_id))

    with tenant_session(tenant) as session:
        occ = session.get(models.FindingOccurrence, occ_id)
        assert occ.run_asset_id is None
