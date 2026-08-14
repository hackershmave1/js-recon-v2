"""Tests for the central login (`/auth/login`, `/auth/me`) and the tenant seam.

Hermetic cases (503 unconfigured, 401 bad/no token) gate in the fast lane; the
login-success + seam paths need live PG for the user lookup.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.auth import service as auth_service
from recon.auth import token as auth_token
from recon.config import get_settings
from recon.pairing import token as pairing_token

AUTH_KEY = "auth-test-secret"


@pytest.fixture()
def make_auth_client(monkeypatch):
    def _make(**env) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()
        return TestClient(create_app())

    yield _make
    get_settings.cache_clear()


def _seed_unique_user(password: str = "admin", role: str = "admin") -> tuple[str, str, str]:
    """Seed a fresh tenant + a globally-unique username; return (tenant_id, user_id,
    username). Unique so a cross-test duplicate never trips AmbiguousUser."""
    tenant_id = str(uuid.uuid4())
    username = f"admin-{uuid.uuid4().hex[:8]}"
    user_id = auth_service.seed_admin(
        username=username,
        password=password,
        tenant_id=tenant_id,
        tenant_name="auth-test",
        role=role,
    )
    return tenant_id, user_id, username


# --------------------------- hermetic (no DB) --------------------------- #


def test_login_is_503_when_auth_disabled(make_auth_client):
    r = make_auth_client().post("/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 503


def test_me_is_401_without_a_token(make_auth_client):
    r = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY).get("/auth/me")
    assert r.status_code == 401


def test_me_is_401_with_a_garbage_token(make_auth_client):
    c = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY)
    r = c.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_pairing_token_is_not_accepted_as_a_login(make_auth_client):
    # Domain separation at the HTTP boundary: a pairing token (even signed with the
    # auth key) can't authenticate a session route.
    c = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY)
    paired = pairing_token.mint(str(uuid.uuid4()), key=AUTH_KEY, ttl_seconds=3600)
    r = c.get("/auth/me", headers={"Authorization": f"Bearer {paired}"})
    assert r.status_code == 401


def test_capture_rejects_tokenless_ingest_when_auth_enabled(monkeypatch):
    # Review Finding 5: with auth configured, a tokenless capture is rejected (fail closed)
    # regardless of allow_anon_capture — never falls back to the shared tenant. Raises before
    # any DB work, so this stays hermetic.
    from fastapi import HTTPException

    from recon.api import capture_router

    monkeypatch.setenv("RECON_AUTH_SECRET", AUTH_KEY)
    monkeypatch.setenv("RECON_ALLOW_ANON_CAPTURE", "true")  # even explicitly on, auth wins
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as exc:
            capture_router._resolve_ingest_tenant(None)
        assert exc.value.status_code == 401
    finally:
        get_settings.cache_clear()


# --------------------- integration (live PG) --------------------- #


@pytest.mark.integration
def test_login_success_returns_a_working_token(make_auth_client):
    tenant_id, user_id, username = _seed_unique_user(password="s3cret")
    c = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY)

    r = c.post("/auth/login", json={"username": username, "password": "s3cret"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant"]["id"] == tenant_id
    assert body["role"] == "admin"
    # The minted token verifies to exactly this principal.
    claims = auth_token.verify(body["token"], key=AUTH_KEY)
    assert claims is not None and claims.user_id == user_id and claims.tenant_id == tenant_id

    me = c.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == user_id


@pytest.mark.integration
def test_login_is_case_insensitive(make_auth_client):
    # Seeded lowercase; a login typed in a different case (and with surrounding
    # whitespace) still authenticates — usernames are case-insensitive
    # (recon.auth.service.normalize_username, applied on both seed write and login read).
    _tenant_id, user_id, username = _seed_unique_user(password="s3cret")
    c = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY)
    r = c.post("/auth/login", json={"username": f"  {username.upper()}  ", "password": "s3cret"})
    assert r.status_code == 200, r.text
    claims = auth_token.verify(r.json()["token"], key=AUTH_KEY)
    assert claims is not None and claims.user_id == user_id


@pytest.mark.integration
def test_login_wrong_password_is_401(make_auth_client):
    _tenant_id, _user_id, username = _seed_unique_user(password="s3cret")
    c = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY)
    r = c.post("/auth/login", json={"username": username, "password": "wrong"})
    assert r.status_code == 401


@pytest.mark.integration
def test_login_unknown_user_is_401(make_auth_client):
    c = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY)
    r = c.post("/auth/login", json={"username": f"ghost-{uuid.uuid4().hex}", "password": "x"})
    assert r.status_code == 401


@pytest.mark.integration
def test_tenant_route_requires_login_when_auth_enabled(make_auth_client):
    _tenant_id, _user_id, username = _seed_unique_user(password="s3cret")
    c = make_auth_client(RECON_AUTH_SECRET=AUTH_KEY)

    # No token → the tenant seam rejects (get_tenant_id, auth on).
    assert c.get("/sessions").status_code == 401

    token = c.post("/auth/login", json={"username": username, "password": "s3cret"}).json()["token"]
    r = c.get("/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text  # tenant resolved from the token
