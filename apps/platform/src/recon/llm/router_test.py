"""Per-session LLM config routes. The /test case must actually reach _ping."""

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.llm import service as llm_service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


class _StubProvider:
    model = "stub-model"

    async def generate_structured(self, **_kwargs):
        return None


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_session_key_test_reaches_ping_and_stamps_tested_at(client, tenant, monkeypatch):
    # Regression: test_config used get_event_loop().run_until_complete inside
    # run_in_threadpool, which raises RuntimeError on Python 3.11 worker threads.
    calls = []
    monkeypatch.setattr(
        llm_service,
        "build_provider",
        lambda provider, api_key, model=None: calls.append((provider, api_key)) or _StubProvider(),
    )
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    llm_service.save_config(tenant, sv.id, "anthropic", "m", "k-test")

    r = client.post(f"/sessions/{sv.id}/llm-config/test", headers={"X-Tenant-Id": tenant})

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "provider": "anthropic", "model": "m"}
    assert calls == [("anthropic", "k-test")]
    assert llm_service.get_config(tenant, sv.id)["tested_at"] is not None


def test_session_key_test_without_key_is_400(client, tenant):
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    r = client.post(f"/sessions/{sv.id}/llm-config/test", headers={"X-Tenant-Id": tenant})
    assert r.status_code == 400
    assert r.json()["detail"] == "no config saved"


def test_session_switching_provider_with_blank_key_is_422(client, tenant):
    # A stored key travels with its provider: no blank-key provider switch.
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    llm_service.save_config(tenant, sv.id, "openrouter", "m", "or-key")
    r = client.post(
        f"/sessions/{sv.id}/llm-config",
        json={"provider": "anthropic", "model": "m", "api_key": ""},
        headers={"X-Tenant-Id": tenant},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "a new provider needs its own API key"
    assert llm_service.get_config(tenant, sv.id)["provider"] == "openrouter"


def test_session_same_provider_blank_key_keeps_key(client, tenant):
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    llm_service.save_config(tenant, sv.id, "openrouter", "m", "or-key")
    r = client.post(
        f"/sessions/{sv.id}/llm-config",
        json={"provider": "openrouter", "model": "m2", "api_key": ""},
        headers={"X-Tenant-Id": tenant},
    )
    assert r.status_code == 201, r.text
    assert r.json()["has_key"] is True
