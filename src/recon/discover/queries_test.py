# src/recon/discover/queries_test.py
import json

import pytest

from recon import storage
from recon.db.base import tenant_session
from recon.events.log import record_event
from recon.runs import service as runs_service
from recon.discover import queries

pytestmark = pytest.mark.integration  # DB + object storage; needs the docker stack


def test_latest_assets_event_and_manifest(authorized_session, redis):
    tenant_id, session_id = authorized_session
    run = runs_service.create_run(redis, tenant_id=tenant_id, session_id=session_id, target="acme.io")
    manifest = {"domain": "acme.io", "status": "ok",
                "assets": [{"url": "https://acme.io/a.js", "source": "katana"}]}
    ref = storage.put_blob(tenant_id, run.id, "assets", json.dumps(manifest).encode())
    with tenant_session(tenant_id) as session:
        record_event(session, tenant_id=tenant_id, run_id=run.id,
                     event_type="discover.assets",
                     payload={"count": 1, "assets_ref": ref, "status": "ok"})
    assert queries.latest_assets_event(tenant_id, run.id)["count"] == 1
    assert queries.get_assets_manifest(tenant_id, run.id) == manifest


def test_manifest_none_when_no_event(authorized_session, redis):
    tenant_id, session_id = authorized_session
    run = runs_service.create_run(redis, tenant_id=tenant_id, session_id=session_id, target="acme.io")
    assert queries.get_assets_manifest(tenant_id, run.id) is None
