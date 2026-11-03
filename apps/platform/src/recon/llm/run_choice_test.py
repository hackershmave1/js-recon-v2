"""run_choice: which provider, model and key a session's LLM run uses — and the Threat Model
tab's preset menu, which must always name the model a real run would use."""

import uuid

import pytest
from cryptography.fernet import Fernet

from recon.auth import service as auth_service
from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.llm import run_choice, tenant_config
from recon.llm import service as llm_service
from recon.llm.crypto import KeyDecryptError, encrypt_api_key
from recon.llm.presets import PRESETS
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


def _key(tenant: str, session_id: str, preset: str | None = None) -> tuple:
    choice = run_choice.resolve(tenant, session_id, preset, with_key=True)
    return None if choice is None else (choice.provider, choice.model, choice.api_key)


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


# --- precedence: a key always travels with the provider it belongs to ----------------------


def test_env_openrouter_key_uses_openrouter_provider(session_ids, monkeypatch):
    # The bug: no session config + only OPENROUTER_API_KEY sent the key to Anthropic.
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert _key(*session_ids) == ("openrouter", None, "or-key")


def test_env_anthropic_key_uses_anthropic_provider(session_ids, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
    assert _key(*session_ids) == ("anthropic", None, "an-key")


def test_saved_model_kept_only_for_its_own_provider(session_ids, monkeypatch):
    # Session picked a provider + model but saved no key ("" stores none).
    llm_service.save_config(*session_ids, "anthropic", "claude-x", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
    assert _key(*session_ids) == ("anthropic", "claude-x", "an-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert _key(*session_ids) == ("openrouter", None, "or-key")


def test_saved_session_key_wins_over_env(session_ids, monkeypatch):
    llm_service.save_config(*session_ids, "anthropic", "claude-x", "session-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert _key(*session_ids) == ("anthropic", "claude-x", "session-key")


def test_no_saved_key_and_no_env_key_is_none(session_ids):
    assert run_choice.resolve(*session_ids, with_key=True) is None
    assert run_choice.preset_menu(*session_ids) == {"credential_provider": None, "presets": None}


def test_session_key_beats_team_key(session_ids):
    tenant, session_id = session_ids
    _save_team(tenant, "openrouter", "team-model", "team-key")
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "session-key")
    assert _key(tenant, session_id) == ("anthropic", "claude-x", "session-key")


def test_team_key_beats_env_and_keeps_its_own_provider(session_ids, monkeypatch):
    tenant, session_id = session_ids
    # The session picked anthropic + a model but saved no key: the team key must NOT
    # borrow the session's provider or model.
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "")
    _save_team(tenant, "openrouter", "team-model", "team-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert _key(tenant, session_id) == ("openrouter", "team-model", "team-key")


def test_env_used_when_no_team_key(session_ids, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert _key(*session_ids) == ("anthropic", None, "env-key")


def test_unknown_session_is_none(session_ids):
    tenant, _ = session_ids
    assert run_choice.resolve(tenant, str(uuid.uuid4()), with_key=True) is None


# --- source + key handling --------------------------------------------------------------------


def test_source_names_whose_key_pays(session_ids, monkeypatch):
    tenant, session_id = session_ids
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert run_choice.resolve(tenant, session_id).source == "env"
    _save_team(tenant, "openrouter", "team-model", "team-key")
    assert run_choice.resolve(tenant, session_id).source == "team"
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "session-key")
    assert run_choice.resolve(tenant, session_id).source == "session"


def test_without_key_never_returns_key_material(session_ids):
    tenant, session_id = session_ids
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "session-key")
    assert run_choice.resolve(tenant, session_id).api_key is None


# --- the preset menu and real runs always agree ------------------------------------------------


def _admin(tenant: str) -> str:
    return auth_service.seed_admin(
        username=f"a-{uuid.uuid4().hex[:8]}",
        password="pw",
        tenant_id=tenant,
        tenant_name="t",
        role="admin",
    )


def _setup_session_key(tenant, session_id, monkeypatch):
    llm_service.save_config(tenant, session_id, "openrouter", "anthropic/claude-sonnet-4.6", "k")


def _setup_session_provider_only_and_env(tenant, session_id, monkeypatch):
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")


def _setup_team_key_with_overrides(tenant, session_id, monkeypatch):
    tenant_config.save_config(
        tenant, _admin(tenant), "openrouter", "team-model", "k", {"strongest": "vendor/best"}
    )


def _setup_session_key_on_other_provider_than_team(tenant, session_id, monkeypatch):
    # Team overrides are for the team's provider; a session key on another provider must not
    # pick them up — in the menu or in the run.
    tenant_config.save_config(
        tenant, _admin(tenant), "openrouter", "team-model", "k", {"strongest": "vendor/best"}
    )
    llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "session-key")


def _setup_env_openrouter(tenant, session_id, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")


@pytest.mark.parametrize(
    "setup",
    [
        _setup_session_key,
        _setup_session_provider_only_and_env,
        _setup_team_key_with_overrides,
        _setup_session_key_on_other_provider_than_team,
        _setup_env_openrouter,
    ],
)
def test_menu_names_the_model_a_real_run_uses(session_ids, monkeypatch, setup):
    tenant, session_id = session_ids
    setup(tenant, session_id, monkeypatch)
    menu = run_choice.preset_menu(tenant, session_id)
    for preset in PRESETS:
        run = run_choice.resolve(tenant, session_id, preset, with_key=True)
        assert menu["credential_provider"] == run.provider
        assert menu["presets"][preset] == run.model, preset


@pytest.mark.parametrize("level", ["session", "team"])
def test_undecryptable_key_menu_still_names_provider_and_run_fails_loudly(
    session_ids, monkeypatch, level
):
    # Today's behaviour, kept: the menu never decrypts, so it shows the provider; the real run
    # raises KeyDecryptError (-> "re-save it"), it does NOT fall through to another key.
    tenant, session_id = session_ids
    if level == "session":
        llm_service.save_config(tenant, session_id, "anthropic", "claude-x", "session-key")
    else:
        tenant_config.save_config(
            tenant, _admin(tenant), "openrouter", "team-model", "k", {"strongest": "vendor/best"}
        )
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")  # must not be used as a fallback
    monkeypatch.setenv("RECON_LLM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        menu = run_choice.preset_menu(tenant, session_id)
        assert menu["credential_provider"] == ("anthropic" if level == "session" else "openrouter")
        with pytest.raises(KeyDecryptError):
            run_choice.resolve(tenant, session_id, "strongest", with_key=True)
    finally:
        get_settings.cache_clear()


def test_preset_resolves_for_the_credentials_provider(session_ids):
    tenant, session_id = session_ids
    llm_service.save_config(tenant, session_id, "openrouter", "anthropic/claude-sonnet-4.6", "k")
    assert _key(tenant, session_id, "cheapest") == (
        "openrouter",
        "anthropic/claude-haiku-4.5:floor",
        "k",
    )


def test_team_override_used_for_the_team_key(session_ids):
    tenant, session_id = session_ids
    tenant_config.save_config(
        tenant, _admin(tenant), "openrouter", "team-model", "k", {"strongest": "vendor/best"}
    )
    assert _key(tenant, session_id, "strongest") == ("openrouter", "vendor/best", "k")
