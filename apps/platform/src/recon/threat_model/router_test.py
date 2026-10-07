"""Threat-model routes: preset validation and the per-run presets endpoint."""

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.llm import service as llm_service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for env_var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)


def _session(tenant: str) -> str:
    return sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id


def test_unknown_preset_is_422(client, tenant):
    sid = _session(tenant)
    r = client.post(
        f"/sessions/{sid}/threat-model", json={"preset": "turbo"}, headers={"X-Tenant-Id": tenant}
    )
    assert r.status_code == 422


def test_presets_endpoint_resolves_for_the_credential_provider(client, tenant):
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "openrouter", "anthropic/claude-sonnet-4.6", "k")
    r = client.get(f"/sessions/{sid}/threat-model/presets", headers={"X-Tenant-Id": tenant})
    assert r.status_code == 200
    assert r.json() == {
        "credential_provider": "openrouter",
        "presets": {
            "cheapest": "anthropic/claude-haiku-4.5:floor",
            "balanced": "anthropic/claude-sonnet-4.6",
            "strongest": "anthropic/claude-opus-5.5",
        },
    }


def test_presets_endpoint_without_any_key(client, tenant):
    sid = _session(tenant)
    r = client.get(f"/sessions/{sid}/threat-model/presets", headers={"X-Tenant-Id": tenant})
    assert r.json() == {"credential_provider": None, "presets": None}
