"""Fast-lane unit tests for the ``get_actor`` dependency (D48).

No database or Redis — these are pure logic tests that verify actor derivation
from a JWT vs. falling back to None when auth is off or the token is invalid.
"""

from __future__ import annotations

from recon.api import deps
from recon.auth import token as auth_token
from recon.config import get_settings

AUTH_KEY = "actor-test-secret"
TENANT_ID = "00000000-0000-0000-0000-000000000042"
USER_ID = "operator-abc"


def _mint_token(user_id: str = USER_ID, tenant_id: str = TENANT_ID) -> str:
    return auth_token.mint(
        user_id=user_id,
        tenant_id=tenant_id,
        role="admin",
        key=AUTH_KEY,
        ttl_seconds=3600,
    )


def test_get_actor_returns_user_id_from_valid_token(monkeypatch):
    """When auth is on and the token is valid, actor is derived from the JWT."""
    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    get_settings.cache_clear()
    try:
        token = _mint_token()
        result = deps.get_actor(authorization=f"Bearer {token}")
        assert result == USER_ID
    finally:
        get_settings.cache_clear()


def test_get_actor_returns_none_when_auth_off(monkeypatch):
    """When auth is disabled (no secret), actor is None — dev mode is unblocked."""
    monkeypatch.delenv("RECON_AUTH_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        result = deps.get_actor(authorization=None)
        assert result is None
    finally:
        get_settings.cache_clear()


def test_get_actor_returns_none_when_no_header(monkeypatch):
    """With auth on but no Authorization header, actor is None (not a 401)."""
    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    get_settings.cache_clear()
    try:
        result = deps.get_actor(authorization=None)
        assert result is None
    finally:
        get_settings.cache_clear()


def test_get_actor_returns_none_on_invalid_token(monkeypatch):
    """With auth on but a bad/tampered token, actor is None (not a 401)."""
    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    get_settings.cache_clear()
    try:
        result = deps.get_actor(authorization="Bearer not-a-real-token")
        assert result is None
    finally:
        get_settings.cache_clear()


def test_get_actor_returns_none_on_wrong_key(monkeypatch):
    """A token signed with a different key is rejected; actor is None."""
    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    get_settings.cache_clear()
    try:
        # Minted with a different key — verification will fail
        token = auth_token.mint(
            user_id=USER_ID, tenant_id=TENANT_ID, role="admin",
            key="wrong-key", ttl_seconds=3600,
        )
        result = deps.get_actor(authorization=f"Bearer {token}")
        assert result is None
    finally:
        get_settings.cache_clear()
