"""load_credentials: a key must always travel with the provider it belongs to."""

import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.llm import service as llm_service
from recon.llm.crypto import encrypt_api_key
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture
def session_ids(monkeypatch):
    for env_var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    tenant = sessions_service.create_tenant("llm-creds")
    sv = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    return tenant, sv.id


def test_env_openrouter_key_uses_openrouter_provider(session_ids, monkeypatch):
    # The bug: no session config + only OPENROUTER_API_KEY sent the key to Anthropic.
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert llm_service.load_credentials(*session_ids) == ("openrouter", None, "or-key")


def test_env_anthropic_key_uses_anthropic_provider(session_ids, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
    assert llm_service.load_credentials(*session_ids) == ("anthropic", None, "an-key")


def test_saved_model_kept_only_for_its_own_provider(session_ids, monkeypatch):
    # Session picked a provider + model but saved no key ("" stores none).
    llm_service.save_config(*session_ids, "anthropic", "claude-x", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
    assert llm_service.load_credentials(*session_ids) == ("anthropic", "claude-x", "an-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert llm_service.load_credentials(*session_ids) == ("openrouter", None, "or-key")


def test_saved_session_key_wins_over_env(session_ids, monkeypatch):
    llm_service.save_config(*session_ids, "anthropic", "claude-x", "session-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert llm_service.load_credentials(*session_ids) == ("anthropic", "claude-x", "session-key")


def test_no_saved_key_and_no_env_key_is_none(session_ids):
    assert llm_service.load_credentials(*session_ids) is None


def _save_team(tenant: str, provider: str, model: str, key: str) -> None:
    with tenant_session(tenant) as db:
        db.add(
            models.TenantLlmConfig(
                tenant_id=tenant,
                provider=provider,
                model=model,
                encrypted_api_key=encrypt_api_key(key),
            )
        )


def test_session_key_beats_team_key(session_ids):
    tenant, session_id = session_ids
    _save_team(tenant, "openrouter", "team-model", "team-key")
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "session-key")
    assert llm_service.load_credentials(tenant, session_id) == (
        "anthropic",
        "claude-x",
        "session-key",
    )


def test_team_key_beats_env_and_keeps_its_own_provider(session_ids, monkeypatch):
    tenant, session_id = session_ids
    # The session picked anthropic + a model but saved no key: the team key must NOT
    # borrow the session's provider or model.
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "")
    _save_team(tenant, "openrouter", "team-model", "team-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert llm_service.load_credentials(tenant, session_id) == (
        "openrouter",
        "team-model",
        "team-key",
    )


def test_env_used_when_no_team_key(session_ids, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert llm_service.load_credentials(*session_ids) == ("anthropic", None, "env-key")
