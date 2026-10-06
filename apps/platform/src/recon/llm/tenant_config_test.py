"""Team-wide LLM config service: admin-only writes (role checked in the DB), key never serialized."""

import uuid

import pytest

from recon.auth import service as auth_service
from recon.llm import tenant_config

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
