"""Team-wide LLM config service: admin-only writes (role checked in the DB), key never serialized."""

import uuid

import pytest
from cryptography.fernet import Fernet

from recon.auth import service as auth_service
from recon.config import get_settings
from recon.llm import tenant_config
from recon.llm.crypto import KeyDecryptError

pytestmark = pytest.mark.integration


def _seed_user(role: str) -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    user_id = auth_service.seed_admin(
        username=f"u-{uuid.uuid4().hex[:8]}",
        password="pw",
        tenant_id=tenant_id,
        tenant_name="team-llm",
        role=role,
    )
    return tenant_id, user_id


def test_admin_round_trip_and_key_never_serialized():
    tenant, admin = _seed_user("admin")
    saved = tenant_config.save_config(tenant, admin, "openrouter", "m1", "secret-key-123")
    assert saved is not None
    assert saved["has_key"] is True and saved["provider"] == "openrouter"
    assert "secret-key-123" not in repr(saved)
    got = tenant_config.get_config(tenant, include_actor=True)
    assert got["configured_by"] is not None and "secret-key-123" not in repr(got)
    assert tenant_config.load_key(tenant) == ("openrouter", "m1", "secret-key-123")


def test_empty_key_keeps_the_stored_key():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m1", "k1")
    tenant_config.save_config(tenant, admin, "anthropic", "m2", "")
    assert tenant_config.load_key(tenant) == ("anthropic", "m2", "k1")


def test_switching_provider_with_blank_key_is_rejected():
    # The stored key belongs to openrouter; keeping it under anthropic would send it there.
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "m1", "or-key")
    with pytest.raises(ValueError, match="a new provider needs its own API key"):
        tenant_config.save_config(tenant, admin, "anthropic", "m2", "")
    assert tenant_config.load_key(tenant) == ("openrouter", "m1", "or-key")


def test_analyst_cannot_write():
    tenant, analyst = _seed_user("analyst")
    assert tenant_config.save_config(tenant, analyst, "anthropic", "m", "k") is None
    assert tenant_config.delete_config(tenant, analyst) is None
    assert tenant_config.get_config(tenant, include_actor=True) is None


def test_actor_hidden_without_include_actor():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m", "k")
    assert tenant_config.get_config(tenant, include_actor=False)["configured_by"] is None


def test_delete_then_nothing():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m", "k")
    assert tenant_config.delete_config(tenant, admin) is True
    assert tenant_config.delete_config(tenant, admin) is False
    assert tenant_config.load_key(tenant) is None


def test_mark_tested_stamps():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "anthropic", "m", "k")
    tenant_config.mark_tested(tenant)
    assert tenant_config.get_config(tenant, include_actor=False)["tested_at"] is not None


def test_preset_models_omitted_keeps_null_clears_map_replaces():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k", {"strongest": "vendor/big"})
    tenant_config.save_config(tenant, admin, "openrouter", "m2", "")  # omitted -> keep
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {
        "strongest": "vendor/big"
    }
    tenant_config.save_config(tenant, admin, "openrouter", "m2", "", {"cheapest": "vendor/small"})
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {
        "cheapest": "vendor/small"
    }
    tenant_config.save_config(tenant, admin, "openrouter", "m2", "", None)  # null -> clear
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {}


def test_provider_change_clears_overrides_unless_new_ones_are_sent():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k", {"balanced": "vendor/x"})
    tenant_config.save_config(tenant, admin, "anthropic", "claude-x", "k2")
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {}
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k3", {"balanced": "vendor/y"})
    assert tenant_config.get_config(tenant, include_actor=False)["preset_models"] == {
        "balanced": "vendor/y"
    }


def test_preset_context_and_key_provider_never_decrypt(monkeypatch):
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "m", "k", {"cheapest": "vendor/c"})
    monkeypatch.setenv("RECON_LLM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        # The stored (cleartext-mode) key can't be decrypted under the new key.
        with pytest.raises(KeyDecryptError):
            tenant_config.load_key(tenant)
        assert tenant_config.load_preset_context(tenant) == ("openrouter", {"cheapest": "vendor/c"})
        assert tenant_config.team_key_provider(tenant) == "openrouter"
    finally:
        get_settings.cache_clear()


def test_preset_context_without_team_config():
    tenant, _ = _seed_user("admin")
    assert tenant_config.load_preset_context(tenant) == (None, {})
    assert tenant_config.team_key_provider(tenant) is None


def test_preset_only_save_keeps_the_tested_stamp_and_audit_fields_until_the_model_changes():
    tenant, admin = _seed_user("admin")
    tenant_config.save_config(tenant, admin, "openrouter", "vendor/m", "k")
    tenant_config.mark_tested(tenant)
    before = tenant_config.get_config(tenant, include_actor=True)
    assert before["tested_at"] is not None
    # What the Settings preset Edit/Reset sends: same provider/model, blank key, a preset map.
    after = tenant_config.save_config(
        tenant, admin, "openrouter", "vendor/m", "", {"cheapest": "vendor/small"}
    )
    assert after["preset_models"] == {"cheapest": "vendor/small"}
    assert after["tested_at"] == before["tested_at"]
    assert after["configured_at"] == before["configured_at"]
    assert after["configured_by"] == before["configured_by"]
    changed = tenant_config.save_config(tenant, admin, "openrouter", "vendor/other", "")
    assert changed["tested_at"] is None
    assert changed["configured_at"] != before["configured_at"]
