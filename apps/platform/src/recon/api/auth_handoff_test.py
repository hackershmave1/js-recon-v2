"""ADR-0011 conditions at the HTTP layer. Hermetic cases patch the user lookup; the
``integration`` case hits real Postgres (the user row for the DB re-read)."""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient
from redis import RedisError

from recon.api import deps
from recon.api.app import create_app
from recon.auth import handoff, service
from recon.auth import token as auth_token
from recon.config import get_settings

KEY = "handoff-api-secret"
USER = str(uuid.uuid4())
TENANT = str(uuid.uuid4())


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("RECON_AUTH_SECRET", KEY)
    get_settings.cache_clear()
    redis = fakeredis.FakeRedis()
    c = TestClient(create_app())
    c.app.dependency_overrides[deps.get_login_redis] = lambda: redis
    c.redis = redis  # type: ignore[attr-defined]
    monkeypatch.setattr(
        service,
        "lookup_user",
        lambda user_id: (
            service.UserRecord(user_id, TENANT, "analyst-now", "researcher@example.test")
            if user_id == USER
            else None
        ),
    )
    monkeypatch.setattr(service, "tenant_name", lambda tid: "Acme (test)")
    yield c
    c.app.dependency_overrides.clear()
    get_settings.cache_clear()


def _bearer(ttl: int = 3600, role: str = "admin") -> dict[str, str]:
    tok = auth_token.mint(user_id=USER, tenant_id=TENANT, role=role, key=KEY, ttl_seconds=ttl)
    return {"Authorization": f"Bearer {tok}"}


def _issue(c: TestClient, **kw) -> str:
    r = c.post("/auth/handoff", headers=_bearer(**kw))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()["code"]


def test_issue_requires_a_login_token(client) -> None:
    assert client.post("/auth/handoff").status_code == 401
    assert client.post("/auth/handoff", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_issue_and_exchange_roundtrip_and_second_use_fails(client) -> None:
    code = _issue(client)
    r = client.post("/auth/handoff/exchange", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    # Role and username come from the DB re-read, not from the issuing token.
    assert body["role"] == "analyst-now" and body["user"] == "researcher@example.test"
    assert body["tenant"] == {"id": TENANT, "name": "Acme (test)"}
    claims = auth_token.verify(body["token"], key=KEY)
    assert claims is not None and (claims.user_id, claims.tenant_id) == (USER, TENANT)
    assert client.post("/auth/handoff/exchange", json={"code": code}).status_code == 401


def test_exchanged_session_never_outlives_the_issuing_token(client) -> None:
    code = _issue(client, ttl=120)
    body = client.post("/auth/handoff/exchange", json={"code": code}).json()
    claims = auth_token.verify(body["token"], key=KEY)
    assert claims is not None and claims.exp <= int(time.time()) + 120
    assert claims.exp < int(time.time()) + get_settings().auth_token_ttl_seconds


def test_code_cannot_be_used_after_its_issuing_session_ended(client) -> None:
    code = _issue(client, ttl=1)
    time.sleep(1.2)
    assert client.post("/auth/handoff/exchange", json={"code": code}).status_code == 401


def test_every_failure_is_the_same_generic_401(client) -> None:
    unknown = client.post("/auth/handoff/exchange", json={"code": "A" * 43})
    used_code = _issue(client)
    client.post("/auth/handoff/exchange", json={"code": used_code})
    used = client.post("/auth/handoff/exchange", json={"code": used_code})
    assert unknown.status_code == used.status_code == 401
    assert unknown.json() == used.json() == {"detail": "invalid or expired code"}


def test_wrong_tenant_binding_is_refused(client, monkeypatch) -> None:
    code = _issue(client)
    other = str(uuid.uuid4())
    monkeypatch.setattr(
        service,
        "lookup_user",
        lambda user_id: service.UserRecord(user_id, other, "analyst", "researcher@example.test"),
    )
    assert client.post("/auth/handoff/exchange", json={"code": code}).status_code == 401


def test_deleted_user_is_refused(client, monkeypatch) -> None:
    code = _issue(client)
    monkeypatch.setattr(service, "lookup_user", lambda user_id: None)
    assert client.post("/auth/handoff/exchange", json={"code": code}).status_code == 401


def test_malformed_code_is_rejected_before_redis(client) -> None:
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("Redis must not be touched")

    client.app.dependency_overrides[deps.get_login_redis] = lambda: Boom()
    for bad in ("", "short", "A" * 44, "!" * 43):
        assert client.post("/auth/handoff/exchange", json={"code": bad}).status_code == 422


def test_issue_is_rate_limited(client) -> None:
    for _ in range(handoff.ISSUE_LIMIT):
        _issue(client)
    assert client.post("/auth/handoff", headers=_bearer()).status_code == 429


def test_no_global_exchange_failure_lockout(client) -> None:
    for _ in range(100):
        assert client.post("/auth/handoff/exchange", json={"code": "A" * 43}).status_code == 401
    assert client.post("/auth/handoff/exchange", json={"code": _issue(client)}).status_code == 200


def test_503_when_auth_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("RECON_AUTH_SECRET", "")
    get_settings.cache_clear()
    c = TestClient(create_app())
    assert c.post("/auth/handoff").status_code == 503
    assert c.post("/auth/handoff/exchange", json={"code": "A" * 43}).status_code == 503
    get_settings.cache_clear()


def test_503_when_redis_is_down(client) -> None:
    class Down:
        def __getattr__(self, name):
            raise RedisError("down")

    client.app.dependency_overrides[deps.get_login_redis] = lambda: Down()
    assert client.post("/auth/handoff", headers=_bearer()).status_code == 503
    assert client.post("/auth/handoff/exchange", json={"code": "A" * 43}).status_code == 503


def test_code_never_appears_in_logs(client, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        code = _issue(client)
        client.post("/auth/handoff/exchange", json={"code": code})
        client.post("/auth/handoff/exchange", json={"code": code})
        client.post("/auth/handoff/exchange", json={"code": code[:-1]})
    assert code not in caplog.text
    assert code[:-1] not in caplog.text


def test_planted_unsigned_redis_value_cannot_log_anyone_in(client) -> None:
    code = "Z" * 43
    blob = f'{{"typ":"handoff","sub":"{USER}","t":"{TENANT}","exp":9999999999}}'.encode()
    client.redis.set(f"handoff:code:{hashlib.sha256(code.encode()).hexdigest()}", blob)
    assert client.post("/auth/handoff/exchange", json={"code": code}).status_code == 401


def test_validation_error_does_not_echo_the_submitted_code(client) -> None:
    r = client.post("/auth/handoff/exchange", json={"code": "nope-xxxxx"})
    assert r.status_code == 422
    assert "nope-xxxxx" not in r.text
    r = client.post("/auth/handoff/exchange", json={"wrong": "nope-yyyyy"})
    assert r.status_code == 422 and "nope-yyyyy" not in r.text


def test_exchange_without_a_json_content_type_is_not_accepted(client) -> None:
    # A web page must not be able to fire the exchange as a CORS-simple request.
    r = client.post(
        "/auth/handoff/exchange",
        content='{"code":"' + "A" * 43 + '"}',
        headers={"Content-Type": "text/plain"},
    )
    assert r.status_code in (415, 422)


@pytest.mark.integration
def test_real_user_roundtrip_reads_role_from_the_database() -> None:
    tenant_id = str(uuid.uuid4())
    username = f"hand-{uuid.uuid4().hex[:8]}"
    user_id = service.seed_admin(
        username=username,
        password="pw-test",
        tenant_id=tenant_id,
        tenant_name="hand-test",
        role="admin",
    )
    os.environ["RECON_AUTH_SECRET"] = KEY
    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        token = auth_token.mint(
            user_id=user_id, tenant_id=tenant_id, role="stale-role", key=KEY, ttl_seconds=600
        )
        r = c.post("/auth/handoff", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        ex = c.post("/auth/handoff/exchange", json={"code": r.json()["code"]})
        assert ex.status_code == 200, ex.text
        assert ex.json()["role"] == "admin" and ex.json()["user"] == username
        assert ex.json()["tenant"]["id"] == tenant_id
    finally:
        os.environ.pop("RECON_AUTH_SECRET", None)
        get_settings.cache_clear()
