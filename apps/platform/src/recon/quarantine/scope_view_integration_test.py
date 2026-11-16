"""The Hosts page's scope panel (ADR-0010, S3) against Postgres and the blob store: removing a host
from the scope hides its files and findings and adding it back returns them (no re-capture), the
overview names scope, undecided, ignored and hidden hosts, and the explicit delete is guarded,
reference-counted and irreversible."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from recon import storage
from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import queries, store
from recon.probe import sources
from recon.quarantine import scope_view, service
from recon.sessions import capture_scope
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

APP_JS = b"fetch('/api/orders');"
CDN_JS = b"fetch('/api/cdn/widgets');"
APP = "https://app.acme.io/app.js"
CDN = "https://cdn.acme-static.com/lib.js"


@pytest.fixture()
def world():
    tenant = sessions_service.create_tenant(f"scope-{uuid.uuid4().hex[:8]}")
    view = sessions_service.create_session(
        tenant, name="s", scope_hosts=["acme.io", "acme-static.com"], authorized_by="t"
    )
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for url, data, finding in (
            (APP, APP_JS, "GET /api/orders"),
            (CDN, CDN_JS, "GET /api/cdn/widgets"),
        ):
            key = storage.put_blob(tenant, run_id, "input", data)
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
                value=finding,
                path="input.js",
                occurrence=store.Occurrence(
                    host=None,
                    source_path="input.js",
                    run_asset_id=str(asset.id),
                    asset_url=asset.url,
                ),
                first_stage="analyzing",
            )
    return tenant, view.id, run_id


def _values(tenant, run_id, **kw):
    view = queries.list_findings(tenant, run_id, **kw)
    return {f.value for f in view.findings}, view.hidden


def test_removing_a_host_hides_its_files_and_findings_and_adding_it_back_returns_them(world):
    tenant, session_id, run_id = world
    assert _values(tenant, run_id)[0] == {"GET /api/orders", "GET /api/cdn/widgets"}
    assert {s.path for s in sources.list_sources(tenant, run_id)} == {APP, CDN}

    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")

    shown, hidden = _values(tenant, run_id)
    assert shown == {"GET /api/orders"}
    assert hidden == {"off_scope": 1}  # "Hidden: out of scope", counted, not deleted
    assert {s.path for s in sources.list_sources(tenant, run_id)} == {APP}
    assert {s.path for s in sources.list_sources(tenant, run_id, include_hidden=True)} == {APP, CDN}
    assert _values(tenant, run_id, include_hidden=True)[0] == {
        "GET /api/orders",
        "GET /api/cdn/widgets",
    }

    capture_scope.add_scope_entry(tenant, session_id, "acme-static.com")  # back, no re-capture
    assert _values(tenant, run_id)[0] == {"GET /api/orders", "GET /api/cdn/widgets"}
    assert {s.path for s in sources.list_sources(tenant, run_id)} == {APP, CDN}


def test_a_finding_seen_in_an_in_scope_file_too_stays_shown(world):
    tenant, session_id, run_id = world
    with tenant_session(tenant) as session:
        cdn_asset = session.scalar(select(models.RunAsset).where(models.RunAsset.url == CDN))
        app_asset = session.scalar(select(models.RunAsset).where(models.RunAsset.url == APP))
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /api/shared",
            path="input.js",
            occurrence=store.Occurrence(
                host=None,
                source_path="input.js",
                run_asset_id=str(cdn_asset.id),
                asset_url=cdn_asset.url,
            ),
            first_stage="analyzing",
        )
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /api/shared",
            path="input.js",
            occurrence=store.Occurrence(
                host=None,
                source_path="input.js",
                run_asset_id=str(app_asset.id),
                asset_url=app_asset.url,
            ),
            first_stage="analyzing",
        )
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")
    assert "GET /api/shared" in _values(tenant, run_id)[0]


def test_a_secret_in_a_removed_hosts_file_is_never_hidden(world):
    tenant, session_id, run_id = world
    with tenant_session(tenant) as session:
        cdn_asset = session.scalar(select(models.RunAsset).where(models.RunAsset.url == CDN))
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.SECRET,
            value="aws:sha256:abcd",
            path="input.js",
            occurrence=store.Occurrence(
                host=None,
                source_path="input.js",
                run_asset_id=str(cdn_asset.id),
                asset_url=cdn_asset.url,
            ),
            first_stage="analyzing",
        )
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")
    assert "aws:sha256:abcd" in _values(tenant, run_id)[0]


def test_overview_names_scope_undecided_ignored_and_hidden_hosts(world):
    tenant, session_id, run_id = world
    service.admit(
        tenant_id=tenant,
        session_id=session_id,
        run_id=run_id,
        host="ads.example.net",
        files=[service.FileIn("https://ads.example.net/t.js", b"track();")],
    )
    service.admit(
        tenant_id=tenant,
        session_id=session_id,
        run_id=run_id,
        host="old.example.org",
        files=[service.FileIn("https://old.example.org/o.js", b"old();")],
    )
    service.decide(
        tenant_id=tenant, session_id=session_id, host="old.example.org", decision="ignore"
    )
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")

    view = scope_view.overview(tenant, session_id)
    assert view["scope_hosts"] == ["acme.io"]
    assert [h["host"] for h in view["undecided"]] == ["ads.example.net"]
    assert view["undecided"][0]["files"] == 1 and view["undecided"][0]["expires_at"]
    assert view["ignored"] == ["old.example.org"]
    assert view["hidden"] == [{"host": "cdn.acme-static.com", "files": 1, "findings": 1}]
    assert scope_view.overview(tenant, str(uuid.uuid4())) is None


def test_adding_an_ignored_host_takes_it_off_the_ignore_list(world):
    tenant, session_id, run_id = world
    service.admit(
        tenant_id=tenant,
        session_id=session_id,
        run_id=run_id,
        host="old.example.org",
        files=[service.FileIn("https://old.example.org/o.js", b"old();")],
    )
    service.decide(
        tenant_id=tenant, session_id=session_id, host="old.example.org", decision="ignore"
    )
    assert scope_view.forget_ignored(tenant, session_id, "old.example.org") is True
    assert service.ignored_hosts(tenant_id=tenant, session_id=session_id) == []
    assert scope_view.forget_ignored(tenant, session_id, "old.example.org") is False


# --- explicit delete ------------------------------------------------------------------------------ #


def test_delete_refuses_a_host_still_in_the_scope(world):
    tenant, session_id, _ = world
    with pytest.raises(scope_view.HostInScope):
        scope_view.delete_host_content(tenant, session_id, "cdn.acme-static.com")


def test_delete_removes_files_findings_and_unreferenced_blobs_only_after_removal(world):
    tenant, session_id, run_id = world
    cdn_key = f"{tenant}/{run_id}/input/{hashlib.sha256(CDN_JS).hexdigest()}"
    app_key = f"{tenant}/{run_id}/input/{hashlib.sha256(APP_JS).hexdigest()}"
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")
    result = scope_view.delete_host_content(tenant, session_id, "cdn.acme-static.com")
    assert result == {"files": 1, "findings": 1}
    assert cdn_key not in storage.list_keys(cdn_key) and app_key in storage.list_keys(app_key)
    assert _values(tenant, run_id, include_hidden=True)[0] == {"GET /api/orders"}
    view = scope_view.overview(tenant, session_id)
    assert view["hidden"] == []
    assert scope_view.delete_host_content(tenant, session_id, "cdn.acme-static.com") == {
        "files": 0,
        "findings": 0,
    }


def test_delete_keeps_a_blob_another_file_of_the_run_still_uses(world):
    tenant, session_id, run_id = world
    shared = f"{tenant}/{run_id}/input/{hashlib.sha256(CDN_JS).hexdigest()}"
    with tenant_session(tenant) as session:  # an in-scope file with the very same bytes
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=run_id,
                url="https://app.acme.io/copy.js",
                input_ref=shared,
                fetch_status="ok",
                analyze_status="ok",
            )
        )
    capture_scope.remove_scope_entry(tenant, session_id, "acme-static.com")
    scope_view.delete_host_content(tenant, session_id, "cdn.acme-static.com")
    assert shared in storage.list_keys(shared)


# --- HTTP ----------------------------------------------------------------------------------------- #


def test_the_scope_endpoints_round_trip(world):
    tenant, session_id, run_id = world
    client = TestClient(create_app())
    headers = {"X-Tenant-Id": tenant}
    got = client.get(f"/sessions/{session_id}/scope", headers=headers)
    assert got.status_code == 200 and got.json()["scope_hosts"] == ["acme.io", "acme-static.com"]
    assert client.get(f"/sessions/{uuid.uuid4()}/scope", headers=headers).status_code == 404

    removed = client.delete(f"/sessions/{session_id}/scope/acme-static.com", headers=headers)
    assert removed.status_code == 200 and removed.json()["scope_hosts"] == ["acme.io"]
    assert (
        client.get(f"/sessions/{session_id}/scope", headers=headers).json()["hidden"][0]["host"]
        == "cdn.acme-static.com"
    )

    refused = client.delete(f"/sessions/{session_id}/hosts/app.acme.io/content", headers=headers)
    assert refused.status_code == 409 and refused.json()["detail"]["error"] == "host_in_scope"

    added = client.post(
        f"/sessions/{session_id}/scope", json={"add": "acme-static.com"}, headers=headers
    )
    assert added.status_code == 200 and "acme-static.com" in added.json()["scope_hosts"]
    bad = client.post(f"/sessions/{session_id}/scope", json={"add": "co.uk"}, headers=headers)
    assert bad.status_code == 422

    client.delete(f"/sessions/{session_id}/scope/acme-static.com", headers=headers)
    gone = client.delete(
        f"/sessions/{session_id}/hosts/cdn.acme-static.com/content", headers=headers
    )
    assert gone.status_code == 200 and gone.json() == {"files": 1, "findings": 1}


# --- scope entries stay minimal ------------------------------------------------------------------- #


def _scope(tenant, session_id):
    with tenant_session(tenant) as session:
        return list(session.get(models.EngagementSession, session_id).scope_hosts)


def test_adding_an_apex_drops_the_subdomain_entries_it_covers(world):
    tenant, session_id, _ = world
    capture_scope.add_scope_entry(tenant, session_id, "cdn.example.org")
    assert "cdn.example.org" in _scope(tenant, session_id)
    capture_scope.add_scope_entry(tenant, session_id, "example.org")
    scope = _scope(tenant, session_id)
    assert "example.org" in scope and "cdn.example.org" not in scope
    # So removing the broad entry really takes the host out of scope.
    capture_scope.remove_scope_entry(tenant, session_id, "example.org")
    assert not any(h.endswith("example.org") for h in _scope(tenant, session_id))


def test_add_by_decision_does_not_duplicate_a_covered_host(world):
    tenant, session_id, run_id = world  # scope already holds acme-static.com
    service.admit(
        tenant_id=tenant,
        session_id=session_id,
        run_id=run_id,
        host="img.acme-static.com",
        files=[service.FileIn("https://img.acme-static.com/i.js", b"img();")],
    )
    service.decide(
        tenant_id=tenant, session_id=session_id, host="img.acme-static.com", decision="add"
    )
    assert _scope(tenant, session_id) == ["acme.io", "acme-static.com"]
