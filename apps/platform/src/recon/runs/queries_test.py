"""Tests for the control-interrupt read helper (REQ-A4).

``raise_if_control_requested`` is what the fetch/analyze per-asset loops call on
every iteration to observe a pause/cancel request mid-stage — covered here in
isolation from any stage's actual work.
"""

from __future__ import annotations

import pytest

from recon.domain import RunStage, RunState
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


def test_status_view_exposes_control_flags(redis, authorized_session):
    """The status projection carries pause/cancel intent so the UI's control
    gating survives a reload — and a cooperative pause (flag set while the run
    stays active) must still invalidate the ETag."""
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")

    fresh = queries.get_status(tenant, view.id)
    assert fresh.pause_requested is False
    assert fresh.cancel_requested is False

    # Move to an active state first, so request_pause sets the flag WITHOUT a
    # transition (only a QUEUED run pauses immediately) — this isolates the
    # flag's effect on the ETag from any state change.
    service.transition(
        redis,
        tenant_id=tenant,
        run_id=view.id,
        to_state=RunState.DISCOVERING,
        stage=RunStage.DISCOVERING,
    )
    active = queries.get_status(tenant, view.id)

    service.request_pause(redis, tenant_id=tenant, run_id=view.id)
    intent = queries.get_status(tenant, view.id)
    assert intent.state == active.state  # still active — the pause is cooperative
    assert intent.pause_requested is True
    assert intent.etag != active.etag  # the flag alone invalidates the ETag


def test_status_view_surfaces_classified_failure_not_raw_message(redis, authorized_session):
    """A FAILED run surfaces the classified, SAFE failure subset — category/reason —
    but never run.error['message'], which can embed an internal IP / engine stderr (M1)."""
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    service.transition(
        redis,
        tenant_id=tenant,
        run_id=view.id,
        to_state=RunState.DISCOVERING,
        stage=RunStage.DISCOVERING,
    )
    # Mirror what worker._handle_failure writes for an SSRF block: the message holds
    # the resolved internal IP; the classified fields are the safe projection.
    service.transition(
        redis,
        tenant_id=tenant,
        run_id=view.id,
        to_state=RunState.FAILED,
        extra_values={
            "error": {
                "stage": "fetching",
                "message": "host acme.io resolves to a non-public address: 10.0.0.5",
                "category": "blocked_address",
                "reason": "The target resolved to a non-public address and was blocked by the egress guard.",
                "host": None,
                "http_status": None,
            }
        },
    )
    status = queries.get_status(tenant, view.id)
    assert status.state == "failed"
    assert status.failure_category == "blocked_address"
    assert status.failure_reason and "egress guard" in status.failure_reason
    assert status.failure_host is None
    # M1: the raw message (carrying the internal IP) must not leak into the projection.
    assert "10.0.0.5" not in (status.failure_reason or "")
