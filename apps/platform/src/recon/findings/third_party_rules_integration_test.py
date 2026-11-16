"""A finding only in a library FILE is hidden as third-party, on the app's own host too (Postgres)."""

from __future__ import annotations

import uuid

import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import queries, store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

JQ = "https://app.acme.io/static/jquery-3.6.0.min.js"
APP = "https://app.acme.io/static/app.js"


def test_a_finding_only_in_a_library_file_is_third_party_and_returns_with_include_hidden():
    tenant = sessions_service.create_tenant(f"tp-{uuid.uuid4().hex[:8]}")
    view = sessions_service.create_session(
        tenant, name="t", scope_hosts=["acme.io"], authorized_by="t"
    )
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for url, value in ((JQ, "GET /api/from-jquery"), (APP, "GET /api/from-app")):
            key = storage.put_blob(tenant, run_id, "input", value.encode())
            asset = models.RunAsset(
                tenant_id=tenant,
                run_id=run_id,
                url=url,
                input_ref=key,
                fetch_status="ok",
                analyze_status="ok",
            )
            session.add(asset)
            session.flush()
            store.record_finding(
                session,
                tenant_id=tenant,
                run_id=run_id,
                finding_type=FindingType.ENDPOINT,
                value=value,
                path="input.js",
                occurrence=store.Occurrence(
                    host=None, source_path="input.js", run_asset_id=str(asset.id), asset_url=url
                ),
                first_stage="analyzing",
            )
    shown = queries.list_findings(tenant, run_id)
    assert {f.value for f in shown.findings} == {"GET /api/from-app"}
    assert shown.hidden == {"third_party": 1}
    everything = queries.list_findings(tenant, run_id, include_hidden=True)
    assert {f.value for f in everything.findings} == {"GET /api/from-app", "GET /api/from-jquery"}
