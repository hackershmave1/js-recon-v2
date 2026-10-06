"""load_credentials: a key must always travel with the provider it belongs to."""

import pytest

from recon.llm import service as llm_service
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
