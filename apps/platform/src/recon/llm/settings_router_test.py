"""/settings/llm: anyone in the team reads; only a current DB admin writes."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.auth import service as auth_service
from recon.auth import token as auth_token
from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.llm import catalog, settings_router
from recon.llm import service as llm_service
from recon.llm.catalog_test import SAMPLE
from recon.sessions import service as sessions_service

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
    logged = []
    monkeypatch.setattr(
        settings_router, "log", SimpleNamespace(info=lambda event, **kw: logged.append((event, kw)))
    )
    client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=admin_h,
    )
    r = client.post("/settings/llm/test", headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "provider": "anthropic", "model": "m"}
    assert dict(logged)["llm.team_config.tested"]["model"] == "m"
    assert client.get("/settings/llm", headers=admin_h).json()["config"]["tested_at"] is not None


def test_switching_provider_with_blank_key_is_422(client, team):
    _, admin_h, _ = team
    client.put(
        "/settings/llm",
        json={"provider": "openrouter", "model": "m", "api_key": "or-key"},
        headers=admin_h,
    )
    r = client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": ""},
        headers=admin_h,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "a new provider needs its own API key"
    assert client.get("/settings/llm", headers=admin_h).json()["config"]["provider"] == "openrouter"


def test_header_only_read_cannot_edit_and_hides_actor(client, team, monkeypatch):
    tenant_id, admin_h, _ = team
    client.put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=admin_h,
    )
    monkeypatch.setenv("RECON_ALLOW_HEADER_TENANT", "1")
    get_settings.cache_clear()
    r = client.get("/settings/llm", headers={"X-Tenant-Id": tenant_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["can_edit"] is False
    assert body["config"]["provider"] == "anthropic"
    assert body["config"]["configured_by"] is None


def test_save_with_misconfigured_encryption_key_is_500_not_422(client, team, monkeypatch):
    # Only the provider-switch rule is a client error; a malformed encryption key is a
    # server misconfig and must not surface as a 422 carrying Fernet's message.
    _, admin_h, _ = team
    monkeypatch.setenv("RECON_LLM_ENCRYPTION_KEY", "not-a-fernet-key")
    get_settings.cache_clear()
    r = TestClient(create_app(), raise_server_exceptions=False).put(
        "/settings/llm",
        json={"provider": "anthropic", "model": "m", "api_key": "k"},
        headers=admin_h,
    )
    assert r.status_code == 500
    assert "Fernet" not in r.text


@pytest.fixture()
def stub_catalog(monkeypatch):
    catalog.reset()
    monkeypatch.setattr(
        catalog,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SAMPLE))
        ),
    )
    yield
    catalog.reset()


def test_models_endpoint_returns_filtered_catalog_and_assumed_estimate(client, team, stub_catalog):
    _, admin_h, _ = team
    body = client.get("/settings/llm/models", headers=admin_h).json()
    assert body["available"] is True
    assert len(body["models"]) == 4
    assert body["estimate"] == {
        "prompt_tokens": 20000,
        "completion_tokens": 4000,
        "basis": "assumed",
        "runs": 0,
    }


def test_estimate_averages_real_runs_and_ignores_zero_token_rows(client, team, stub_catalog):
    tenant_id, admin_h, _ = team
    for prompt, completion in ((10000, 2000), (30000, 6000), (0, 0)):
        sv = sessions_service.create_session(
            tenant_id, name="e", scope_hosts=["acme.io"], authorized_by="t"
        )
        with tenant_session(tenant_id) as db:
            db.add(
                models.SessionThreatModel(
                    tenant_id=tenant_id,
                    session_id=sv.id,
                    status="done",
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                )
            )
    est = client.get("/settings/llm/models", headers=admin_h).json()["estimate"]
    assert est == {"prompt_tokens": 20000, "completion_tokens": 4000, "basis": "history", "runs": 2}


def test_settings_show_builtin_then_team_presets_with_availability(client, team, stub_catalog):
    _, admin_h, _ = team
    assert client.get("/settings/llm", headers=admin_h).json()["presets"] is None
    client.put(
        "/settings/llm",
        json={"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6", "api_key": "k"},
        headers=admin_h,
    )
    client.get("/settings/llm/models", headers=admin_h)  # primes the catalog cache
    presets_view = client.get("/settings/llm", headers=admin_h).json()["presets"]
    assert presets_view["cheapest"] == {
        "model": "anthropic/claude-haiku-4.5",
        "source": "builtin",
        "available": True,
    }
    client.put(
        "/settings/llm",
        json={
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "preset_models": {"strongest": "made/up-model"},
        },
        headers=admin_h,
    )
    body = client.get("/settings/llm", headers=admin_h).json()
    assert body["presets"]["strongest"] == {
        "model": "made/up-model",
        "source": "team",
        "available": False,
    }
    assert body["config"]["preset_models"] == {"strongest": "made/up-model"}
    assert body["builtin_preset_models"]["openrouter"]["balanced"] == "anthropic/claude-sonnet-4.6"


def test_preset_models_validation(client, team):
    _, admin_h, _ = team
    base = {"provider": "anthropic", "model": "m", "api_key": "k"}
    bad_key = {**base, "preset_models": {"fastest": "x"}}
    assert client.put("/settings/llm", json=bad_key, headers=admin_h).status_code == 422
    blank = {**base, "preset_models": {"cheapest": "  "}}
    assert client.put("/settings/llm", json=blank, headers=admin_h).status_code == 422
    ok = client.put(
        "/settings/llm", json={**base, "preset_models": {"cheapest": " claude-x "}}, headers=admin_h
    )
    assert ok.status_code == 200 and ok.json()["preset_models"] == {"cheapest": "claude-x"}
    cleared = client.put(
        "/settings/llm", json={**base, "api_key": "", "preset_models": None}, headers=admin_h
    )
    assert cleared.json()["preset_models"] == {}


def test_overlong_model_ids_are_422(client, team):
    _, admin_h, _ = team
    base = {"provider": "anthropic", "model": "m", "api_key": "k"}
    long_id = "x" * 201
    r = client.put("/settings/llm", json={**base, "model": long_id}, headers=admin_h)
    assert r.status_code == 422 and "200" in r.json()["detail"]
    r = client.put(
        "/settings/llm", json={**base, "preset_models": {"cheapest": long_id}}, headers=admin_h
    )
    assert r.status_code == 422 and "200" in r.json()["detail"]
    ok = client.put("/settings/llm", json={**base, "model": "x" * 200}, headers=admin_h)
    assert ok.status_code == 200
