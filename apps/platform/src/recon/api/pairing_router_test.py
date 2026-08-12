"""Tests for the operator-side pairing mint (`POST /pairing`).

Hermetic cases (503 unconfigured, 401 no tenant) gate in the fast lane; the mint
success + unknown-tenant paths need live PG for the tenant-existence check.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from recon.api.app import create_app
from recon.config import get_settings
from recon.db.base import admin_session
from recon.db.models import Tenant
from recon.pairing import token as pairing_token


@pytest.fixture()
def make_pairing_client(monkeypatch):
    def _make(**env) -> TestClient:
        monkeypatch.setenv("RECON_ENABLE_CAPTURE_INGEST", "true")
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()
        return TestClient(create_app())

    yield _make
    get_settings.cache_clear()


def _hdr(tenant_id: str) -> dict:
    return {"X-Tenant-Id": tenant_id}


# --------------------------- hermetic (no DB) --------------------------- #


def test_mint_is_503_when_pairing_key_unset(make_pairing_client):
    # Pairing disabled (empty key) → 503 before any DB work.
    r = make_pairing_client().post("/pairing", headers=_hdr(str(uuid.uuid4())))
    assert r.status_code == 503


def test_mint_requires_tenant_header(make_pairing_client):
    r = make_pairing_client(RECON_PAIRING_KEY="k").post("/pairing")
    assert r.status_code == 401  # get_tenant_id rejects a missing X-Tenant-Id


# --------------------- integration (live PG) --------------------- #


@pytest.mark.integration
def test_mint_issues_a_verifiable_token(make_pairing_client):
    client = make_pairing_client(RECON_PAIRING_KEY="k")
    with admin_session() as session:
        tenant = Tenant(name=f"op-{uuid.uuid4().hex[:8]}")
        session.add(tenant)
        session.flush()
        tenant_id = str(tenant.id)
    r = client.post("/pairing", headers=_hdr(tenant_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ttlSeconds"] > 0 and body["expiresAt"] > 0
    # The minted token verifies back to exactly the minting tenant.
    assert pairing_token.verify(body["token"], key="k") == tenant_id


@pytest.mark.integration
def test_mint_is_404_for_an_unknown_tenant(make_pairing_client):
    # A well-formed but non-existent tenant id must not yield a token that would then
    # FK-fail at ingest.
    r = make_pairing_client(RECON_PAIRING_KEY="k").post("/pairing", headers=_hdr(str(uuid.uuid4())))
    assert r.status_code == 404
