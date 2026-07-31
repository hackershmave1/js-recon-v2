"""Integration test: a NEW run's analyze stage recognizes a session-scoped taught
wrapper (REQ-D5), without any explicit re-extract on that run."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze
from recon.runs import service

pytestmark = pytest.mark.integration


def _teach(tenant, session_id, callee):
    with tenant_session(tenant) as session:
        session.add(models.SessionWrapper(tenant_id=tenant, session_id=session_id, callee=callee))


def test_future_run_analyze_recognizes_taught_wrapper(redis, authorized_session):
    tenant, session_id = authorized_session
    _teach(tenant, session_id, "api")  # config exists BEFORE the run is analyzed

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", b"const api = makeClient(); api.get('/future');")
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))

    analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    with tenant_session(tenant) as session:
        found = {
            f.value: f for f in session.execute(
                select(models.Finding).where(
                    models.Finding.run_id == view.id, models.Finding.type == "endpoint",
                )
            ).scalars()
        }
    assert "GET /future" in found
    assert found["GET /future"].attributes["wrapper"] == "api"
