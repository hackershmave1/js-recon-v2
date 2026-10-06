"""/settings/llm: anyone in the team reads; only a current DB admin writes."""

import uuid

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.auth import service as auth_service
from recon.auth import token as auth_token
from recon.config import get_settings
from recon.llm import service as llm_service

pytestmark = pytest.mark.integration

AUTH_KEY = "settings-test-secret"


@pytest.fixture()
def client(monkeypatch):
    # Local copy of auth_router_test.make_auth_client: auth must be ON for role checks.
    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def _user(tenant_id: str, role: str) -> str:
    return auth_service.seed_admin(
        username=f"u-{uuid.uuid4().hex[:8]}",
        password="pw",
        tenant_id=tenant_id,
        tenant_name="settings",
        role=role,
    )


def _auth(tenant_id: str, user_id: str, role: str) -> dict:
    token = auth_token.mint(
        user_id=user_id, tenant_id=tenant_id, role=role, key=AUTH_KEY, ttl_seconds=600
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def team():
    tenant_id = str(uuid.uuid4())
    admin = _user(tenant_id, "admin")
    analyst = _user(tenant_id, "analyst")
    return tenant_id, _auth(tenant_id, admin, "admin"), _auth(tenant_id, analyst, "analyst")


def test_admin_saves_analyst_reads_without_key(client, team):
    tenant_id, admin_h, analyst_h = team
    r = client.put(
        "/settings/llm",
        json={"provider": "openrouter", "model": "m", "api_key": "sk-x"},
        headers=admin_h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["has_key"] is True
    r = client.get("/settings/llm", headers=analyst_h)
    body = r.json()
    assert r.status_code == 200
    assert body["can_edit"] is False
    assert body["config"]["provider"] == "openrouter"
    assert "sk-x" not in r.text
    assert set(body["providers"]) == {"anthropic", "openrouter", "gemini"}
    assert client.get("/settings/llm", headers=admin_h).json()["can_edit"] is True


def test_analyst_write_is_403(client, team):
    _, _, analyst_h = team
    r = client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=analyst_h,
    )
    assert r.status_code == 403


def test_demoted_admin_token_is_403(client):
    # The token still says admin, but the DB says analyst: the DB wins.
    tenant_id = str(uuid.uuid4())
    user_id = _user(tenant_id, "analyst")
    r = client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=_auth(tenant_id, user_id, "admin"),
    )
    assert r.status_code == 403


def test_bad_provider_is_422(client, team):
    _, admin_h, _ = team
    r = client.put("/settings/llm", json={"provider": "nope", "model": "m"}, headers=admin_h)
    assert r.status_code == 422


def test_delete_then_404(client, team):
    _, admin_h, _ = team
    client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=admin_h,
    )
    assert client.delete("/settings/llm", headers=admin_h).status_code == 204
    assert client.delete("/settings/llm", headers=admin_h).status_code == 404


def test_test_endpoint_reaches_ping(client, team, monkeypatch):
    _, admin_h, _ = team

    class _Stub:
        async def generate_structured(self, **_kw):
            return None

    monkeypatch.setattr(
        llm_service, "build_provider", lambda provider, api_key, model=None: _Stub()
    )
    client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=admin_h,
    )
    r = client.post("/settings/llm/test", headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "provider": "anthropic", "model": "m"}
    assert client.get("/settings/llm", headers=admin_h).json()["config"]["tested_at"] is not None
