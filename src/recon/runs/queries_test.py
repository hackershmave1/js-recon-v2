"""Tests for the control-interrupt read helper (REQ-A4).

``raise_if_control_requested`` is what the fetch/analyze per-asset loops call on
every iteration to observe a pause/cancel request mid-stage — covered here in
isolation from any stage's actual work.
"""

from __future__ import annotations

import pytest

from recon.queue import retry
from recon.runs import queries, service

pytestmark = pytest.mark.integration


def test_no_control_requested_returns_none(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    assert queries.raise_if_control_requested(tenant, view.id) is None


def test_cancel_requested_raises_control_interrupt_cancel(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    service.request_cancel(redis, tenant_id=tenant, run_id=view.id)

    with pytest.raises(retry.ControlInterrupt) as excinfo:
        queries.raise_if_control_requested(tenant, view.id)
    assert excinfo.value.kind == "cancel"


def test_pause_requested_raises_control_interrupt_pause(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    service.request_pause(redis, tenant_id=tenant, run_id=view.id)

    with pytest.raises(retry.ControlInterrupt) as excinfo:
        queries.raise_if_control_requested(tenant, view.id)
    assert excinfo.value.kind == "pause"
