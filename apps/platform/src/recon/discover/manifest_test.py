"""The discover.assets record through its interface: write (store_manifest + record) and read
(latest / read_manifest), against real Postgres + MinIO."""

import json

import pytest

from recon import storage
from recon.db.base import tenant_session
from recon.discover import manifest
from recon.domain import DiscoveryStatus
from recon.events.log import record_event
from recon.runs import service as runs_service

pytestmark = pytest.mark.integration  # DB + object storage; needs the docker stack


@pytest.fixture()
def run_id(authorized_session, redis):
    tenant_id, session_id = authorized_session
    return runs_service.create_run(
        redis, tenant_id=tenant_id, session_id=session_id, target="acme.io"
    ).id


def _write(tenant_id, run_id, **kwargs):
    found = manifest.store_manifest(tenant_id=tenant_id, run_id=run_id, **kwargs)
    with tenant_session(tenant_id) as session:
        event = manifest.record(session, tenant_id=tenant_id, run_id=run_id, found=found)
    return found, event


def test_written_discovery_reads_back(authorized_session, run_id):
    tenant_id, _ = authorized_session
    found, event = _write(
        tenant_id,
        run_id,
        domain="acme.io",
        status=DiscoveryStatus.CAPPED,
        asset_urls=["https://acme.io/a.js", "https://acme.io/b.js"],
        source="katana",
    )
    # The stored event keeps today's wire shape (plain strings, no requests key when none).
    assert event.payload == {"count": 2, "assets_ref": found.assets_ref, "status": "capped"}
    latest = manifest.read_latest(tenant_id, run_id)
    assert latest == found
    assert not latest.is_complete
    assert manifest.read_manifest(latest) == {
        "domain": "acme.io",
        "status": "capped",
        "assets": [
            {"url": "https://acme.io/a.js", "source": "katana"},
            {"url": "https://acme.io/b.js", "source": "katana"},
        ],
    }


def test_empty_observations_are_still_stored_but_none_are_not(authorized_session, run_id):
    # Browser capture always stores its observed requests (correlate then reports "0 observed");
    # an extension that sent none stores nothing (correlate no-ops).
    tenant_id, _ = authorized_session
    kwargs = {"domain": "acme.io", "status": DiscoveryStatus.OK, "asset_urls": [], "source": "x"}
    empty, event = _write(tenant_id, run_id, observed_requests=[], **kwargs)
    assert empty.requests_ref and json.loads(storage.get_blob(empty.requests_ref)) == []
    assert event.payload["requests_ref"] == empty.requests_ref
    none, _ = _write(tenant_id, run_id, observed_requests=None, **kwargs)
    assert none.requests_ref is None
    assert manifest.read_latest(tenant_id, run_id).requests_ref is None  # the newest event wins


def test_no_event_and_unknown_status_stay_distinct(authorized_session, run_id):
    # No discovery = a legacy/upload run (finalizes DONE); an unknown status fails closed.
    tenant_id, _ = authorized_session
    assert manifest.read_latest(tenant_id, run_id) is None
    with tenant_session(tenant_id) as session:
        record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="discover.assets",
            payload={"count": 0, "assets_ref": None, "status": "something-new"},
        )
    found = manifest.read_latest(tenant_id, run_id)
    assert found is not None and found.status is None and not found.is_complete
