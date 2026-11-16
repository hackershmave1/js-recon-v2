"""Overview and Sources must agree on the file counts (L1 / F14): one definition of "files
collected", with the files hidden by scope counted aside so a screen can say why its number differs.

Cases (walkthrough F14): a host removed from the session scope hides its files from Sources but the
Overview still counted them; a recovered original owned by a hidden file was still listed; a
not-fetched asset is listed by Sources but is not "collected"."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from recon import storage
from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import queries, store
from recon.probe import sources
from recon.sessions import capture_scope
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

APP = "https://app.acme.io/app.js"
CDN = "https://cdn.acme-static.com/lib.js"
FAILED = "https://app.acme.io/missing.js"


@pytest.fixture()
def world():
    tenant = sessions_service.create_tenant(f"counts-{uuid.uuid4().hex[:8]}")
    view = sessions_service.create_session(
        tenant, name="s", scope_hosts=["acme.io", "acme-static.com"], authorized_by="t"
    )
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for url, status in ((APP, "ok"), (CDN, "ok"), (FAILED, "failed")):
            key = (
                storage.put_blob(tenant, run_id, "input", url.encode()) if status == "ok" else None
            )
            session.add(
                models.RunAsset(
                    tenant_id=tenant,
                    run_id=run_id,
                    url=url,
                    input_ref=key,
                    fetch_status=status,
                    analyze_status="ok" if status == "ok" else "pending",
                )
            )
        session.flush()
        cdn = session.query(models.RunAsset).filter_by(run_id=run_id, url=CDN).one()
        # An original recovered from the CDN bundle's map, carrying a finding.
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /api/cdn/widgets",
            path="webpack:/src/widgets.js",
            occurrence=store.Occurrence(
                host=None,
                source_path="webpack:/src/widgets.js",
                run_asset_id=str(cdn.id),
                asset_url=cdn.url,
            ),
            first_stage="analyzing",
        )
    return tenant, view.id, run_id


def _collected(tenant, run_id):
    return queries.list_findings(tenant, run_id).files


def test_overview_and_sources_count_the_same_collected_files(world):
    tenant, _, run_id = world
    counts = sources.file_counts(tenant, run_id)
    assert counts is not None
    assert counts.collected == 2  # APP + CDN fetched; the failed fetch is not "collected"
    assert counts.hidden_out_of_scope == 0
    assert counts.collected == _collected(tenant, run_id)


def test_a_removed_host_leaves_the_collected_count_and_is_named_as_hidden(world):
    tenant, session_id, run_id = world
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")
    counts = sources.file_counts(tenant, run_id)
    assert (counts.collected, counts.hidden_out_of_scope) == (1, 1)
    # Same number on the Overview side, and on the run header / Sessions list (one definition).
    view = queries.list_findings(tenant, run_id)
    assert (view.files, view.files_hidden) == (1, 1)
    listed = sources.list_sources(tenant, run_id)
    assert {f.path for f in listed if f.kind == "asset" and f.fetch_status == "ok"} == {APP}


def test_a_recovered_original_of_a_hidden_file_is_hidden_with_it(world):
    tenant, session_id, run_id = world
    assert "webpack:/src/widgets.js" in {f.path for f in sources.list_sources(tenant, run_id)}
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")
    assert "webpack:/src/widgets.js" not in {f.path for f in sources.list_sources(tenant, run_id)}
    assert "webpack:/src/widgets.js" in {
        f.path for f in sources.list_sources(tenant, run_id, include_hidden=True)
    }


def test_counts_report_recovered_originals_with_findings(world):
    tenant, _, run_id = world
    counts = sources.file_counts(tenant, run_id)
    assert counts.recovered_with_findings == 1


def test_the_http_surfaces_carry_the_same_pair(world):
    tenant, session_id, run_id = world
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")
    client = TestClient(create_app())
    headers = {"X-Tenant-Id": tenant}
    findings = client.get(f"/runs/{run_id}/findings", headers=headers).json()
    listed = client.get(f"/runs/{run_id}/sources", headers=headers).json()
    assert findings["files"] == listed["counts"]["collected"] == 1
    assert findings["files_hidden"] == listed["counts"]["hidden_out_of_scope"] == 1
    assert listed["counts"]["recovered_with_findings"] == 0  # its only original is hidden
