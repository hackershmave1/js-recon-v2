"""Shared fixtures for integration tests.

The migration is applied once per session, but only when an integration-marked
test actually runs — so the pure/unit tests still run with no Postgres or Redis.
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from redis import Redis

from recon.config import get_settings
from recon.sessions import service as sessions_service


@pytest.fixture()
def engines_required() -> bool:
    """Whether a missing external engine must FAIL rather than skip (REQ-T4).

    Real-binary contract tests skip gracefully when the engine is absent (a dev
    host without the Go-built Sourcemapper, say). In CI the engines are installed
    on purpose, so ``RECON_REQUIRE_ENGINES=1`` turns "unavailable" into a failure —
    otherwise a broken install or an upstream rename that stops the binary running
    would silently skip and give a false green, defeating the drift gate."""
    return bool(os.environ.get("RECON_REQUIRE_ENGINES"))


@pytest.fixture(scope="session")
def migrated():
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def _apply_migrations(request):
    if request.node.get_closest_marker("integration"):
        request.getfixturevalue("migrated")


@pytest.fixture()
def redis():
    client = Redis.from_url(get_settings().redis_url)
    client.flushdb()
    return client


@pytest.fixture()
def tenant():
    return sessions_service.create_tenant(f"acme-{uuid.uuid4().hex[:8]}")


@pytest.fixture()
def authorized_session(tenant):
    view = sessions_service.create_session(
        tenant, name="engagement", scope_hosts=["acme.io"], authorized_by="tester"
    )
    return tenant, view.id
