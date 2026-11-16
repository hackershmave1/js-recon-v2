"""Team third-party rules against Postgres (ADR-0010, R2): RLS, the API, additions-only, and the
read-time merge in visibility (a team file pattern and a team host hide findings; removing it
brings them back; a typed scope host is never hidden by a host entry)."""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from recon import storage
from recon.api.app import create_app
from recon.auth import service as auth_service
from recon.auth import token as auth_token
from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import queries, store, team_rules, third_party_rules
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

AUTH_KEY = "team-rules-test-secret"
URL = "/settings/third-party-rules"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def _member(tenant_id: str, role: str = "analyst") -> tuple[str, dict]:
    user_id = auth_service.seed_admin(
        username=f"u-{uuid.uuid4().hex[:8]}",
        password="pw",
        tenant_id=tenant_id,
        tenant_name="rules",
        role=role,
    )
    token = auth_token.mint(
        user_id=user_id, tenant_id=tenant_id, role=role, key=AUTH_KEY, ttl_seconds=600
    )
    return user_id, {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def team():
    tenant_id = str(uuid.uuid4())
    user_id, headers = _member(tenant_id)
    return tenant_id, user_id, headers


def test_rls_is_enabled_and_forced_on_the_table():
    with tenant_session(sessions_service.create_tenant("rls-flags")) as session:
        enabled, forced = session.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": "tenant_third_party_rule"},
        ).one()
    assert enabled and forced


def test_a_tenant_never_sees_another_tenants_rules(client, team):
    tenant_a, _, headers_a = team
    tenant_b = str(uuid.uuid4())
    _, headers_b = _member(tenant_b)
    assert (
        client.post(
            URL, json={"kind": "host", "value": "tracker.acme-a.com"}, headers=headers_a
        ).status_code
        == 201
    )
    assert client.get(URL, headers=headers_b).json()["team"]["hosts"] == []
    with tenant_session(tenant_b) as session:  # unfiltered query: only the database can hide it
        assert session.query(models.TenantThirdPartyRule).count() == 0
    assert team_rules.merged_for_extension(tenant_b)["team_hosts"] == []


def test_add_list_remove_with_the_real_actor(client, team):
    tenant_id, user_id, headers = team
    r = client.post(
        URL, json={"kind": "file_pattern", "value": "/Vendor/Chat-Widget*.js"}, headers=headers
    )
    assert r.status_code == 201, r.text
    assert r.json()["value"] == "/vendor/chat-widget*.js"
    rid = r.json()["id"]
    body = client.get(URL, headers=headers).json()
    assert [x["value"] for x in body["team"]["file_patterns"]] == ["/vendor/chat-widget*.js"]
    assert body["team"]["file_patterns"][0]["created_by"]  # the signed-in user's email
    assert set(body["builtin"]["file_patterns"]) == set(third_party_rules.FILE_PATTERNS)
    with tenant_session(tenant_id) as session:
        row = session.query(models.TenantThirdPartyRule).one()
        assert str(row.created_by) == user_id
    assert client.delete(f"{URL}/{rid}", headers=headers).status_code == 204
    assert client.delete(f"{URL}/{rid}", headers=headers).status_code == 404
    assert client.get(URL, headers=headers).json()["team"]["file_patterns"] == []


def test_writes_need_a_signed_in_user(client, team):
    assert client.post(URL, json={"kind": "host", "value": "x.example.com"}).status_code in (
        401,
        403,
    )
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "host", "value": "10.0.0.1"},
        {"kind": "host", "value": "github.io"},
        {"kind": "file_pattern", "value": "*"},
        {"kind": "file_pattern", "value": "/" + "a" * 300},
        {"kind": "other", "value": "x.example.com"},
    ],
)
def test_invalid_rules_are_rejected(client, team, payload):
    assert client.post(URL, json=payload, headers=team[2]).status_code == 422


def test_duplicate_and_built_in_entries_are_409_and_additions_cannot_remove_a_built_in(
    client, team
):
    headers = team[2]
    assert (
        client.post(
            URL, json={"kind": "host", "value": "x.example.com"}, headers=headers
        ).status_code
        == 201
    )
    dup = client.post(URL, json={"kind": "host", "value": "X.Example.com"}, headers=headers)
    assert dup.status_code == 409 and "team list" in dup.json()["detail"]
    builtin = client.post(
        URL, json={"kind": "host", "value": "google-analytics.com"}, headers=headers
    )
    assert builtin.status_code == 409 and "built-in" in builtin.json()["detail"]
    # The built-in list is not rows, so there is nothing to delete: an unknown id is a 404.
    assert client.delete(f"{URL}/{uuid.uuid4()}", headers=headers).status_code == 404
    merged = team_rules.merged_for_extension(team[0])
    assert set(third_party_rules.HOSTS) <= set(merged["hosts"])
    assert set(third_party_rules.FILE_PATTERNS) <= set(merged["file_patterns"])


def test_team_is_capped(client, team, monkeypatch):
    monkeypatch.setattr(team_rules, "MAX_HOSTS", 2)
    headers = team[2]
    for host in ("a1.example.com", "a2.example.com"):
        assert (
            client.post(URL, json={"kind": "host", "value": host}, headers=headers).status_code
            == 201
        )
    assert (
        client.post(
            URL, json={"kind": "host", "value": "a3.example.com"}, headers=headers
        ).status_code
        == 422
    )


def test_extension_download_is_the_merged_list(client, team):
    headers = team[2]
    client.post(URL, json={"kind": "host", "value": "tracker.example.com"}, headers=headers)
    client.post(URL, json={"kind": "file_pattern", "value": "/vendor/chat*.js"}, headers=headers)
    merged = client.get(f"{URL}/merged", headers=headers).json()
    assert "tracker.example.com" in merged["hosts"] and "google-analytics.com" in merged["hosts"]
    assert "/vendor/chat*.js" in merged["file_patterns"]
    assert merged["team_hosts"] == ["tracker.example.com"]


def _run_with_files(tenant: str, scope: list[str], files: dict[str, str]) -> str:
    view = sessions_service.create_session(tenant, name="t", scope_hosts=scope, authorized_by="t")
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for url, value in files.items():
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
                    host=urlsplit(url).hostname,
                    source_path="input.js",
                    run_asset_id=str(asset.id),
                    asset_url=url,
                ),
                first_stage="analyzing",
            )
    return run_id


def test_team_file_pattern_hides_findings_at_read_time_and_removal_brings_them_back(client, team):
    tenant, _, headers = team
    run_id = _run_with_files(
        tenant,
        ["acme.io"],
        {
            "https://app.acme.io/vendor/chat-widget.js": "GET /api/from-chat",
            "https://app.acme.io/static/app.js": "GET /api/from-app",
        },
    )
    assert {f.value for f in queries.list_findings(tenant, run_id).findings} == {
        "GET /api/from-chat",
        "GET /api/from-app",
    }
    rid = client.post(
        URL, json={"kind": "file_pattern", "value": "/vendor/chat*.js"}, headers=headers
    ).json()["id"]
    shown = queries.list_findings(tenant, run_id)
    assert {f.value for f in shown.findings} == {"GET /api/from-app"}
    assert shown.hidden == {"third_party": 1}
    client.delete(f"{URL}/{rid}", headers=headers)
    assert len(queries.list_findings(tenant, run_id).findings) == 2


def test_team_host_hides_an_unscoped_host_but_never_one_typed_into_the_scope(client, team):
    tenant, _, headers = team
    run_id = _run_with_files(
        tenant, ["acme.io"], {"https://cdn.tracker-vendor.com/t.js": "GET /api/vendor"}
    )
    assert queries.list_findings(tenant, run_id).hidden == {"off_scope": 1}
    client.post(URL, json={"kind": "host", "value": "tracker-vendor.com"}, headers=headers)
    shown = queries.list_findings(tenant, run_id)
    assert shown.findings == [] and shown.hidden == {"noise": 1}  # the team host now names it
    # The researcher typed the same host into the scope: a host entry must not hide it.
    scoped = _run_with_files(
        tenant,
        ["acme.io", "tracker-vendor.com"],
        {"https://cdn.tracker-vendor.com/t.js": "GET /api/vendor"},
    )
    assert [f.value for f in queries.list_findings(tenant, scoped).findings] == ["GET /api/vendor"]
