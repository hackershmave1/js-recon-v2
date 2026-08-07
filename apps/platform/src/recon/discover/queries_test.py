# src/recon/discover/queries_test.py
import json

import pytest

from recon import storage
from recon.db.base import tenant_session
from recon.discover import queries
from recon.events.log import record_event
from recon.runs import service as runs_service

pytestmark = pytest.mark.integration  # DB + object storage; needs the docker stack


def test_latest_assets_event_and_manifest(authorized_session, redis):
    tenant_id, session_id = authorized_session
    run = runs_service.create_run(
        redis, tenant_id=tenant_id, session_id=session_id, target="acme.io"
    )
    old = {
        "domain": "acme.io",
        "status": "capped",
        "assets": [{"url": "https://acme.io/old.js", "source": "katana"}],
    }
    new = {
        "domain": "acme.io",
        "status": "ok",
        "assets": [{"url": "https://acme.io/a.js", "source": "katana"}],
    }
    old_ref = storage.put_blob(tenant_id, run.id, "assets", json.dumps(old).encode())
    new_ref = storage.put_blob(tenant_id, run.id, "assets", json.dumps(new).encode())
    with tenant_session(tenant_id) as session:
        record_event(
            session,
            tenant_id=tenant_id,
            run_id=run.id,
            event_type="discover.assets",
            payload={"count": 1, "assets_ref": old_ref, "status": "capped"},
        )
    with tenant_session(tenant_id) as session:
        record_event(
            session,
            tenant_id=tenant_id,
            run_id=run.id,
            event_type="discover.assets",
            payload={"count": 2, "assets_ref": new_ref, "status": "ok"},
        )
    # The newest event (highest run_event.id) wins.
    assert queries.latest_assets_event(tenant_id, run.id)["count"] == 2
    assert queries.get_assets_manifest(tenant_id, run.id) == new


def test_manifest_none_when_no_event(authorized_session, redis):
    tenant_id, session_id = authorized_session
    run = runs_service.create_run(
        redis, tenant_id=tenant_id, session_id=session_id, target="acme.io"
    )
    assert queries.get_assets_manifest(tenant_id, run.id) is None
