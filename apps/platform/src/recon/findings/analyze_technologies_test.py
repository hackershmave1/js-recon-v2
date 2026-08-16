"""Analyze's best-effort per-host tech-detection fingerprint pass (Task 8).

Writes ``run_technology`` from the run's ``fingerprint-signal`` blob (Tasks 6/7),
idempotently (T3), and must NEVER fail the run even when the detector itself
blows up (T2) -- the third test below is the load-bearing one. The fourth test
is the mirror-image regression guard: a cooperative cancel/pause (REQ-A4) is
NOT a failure and must propagate instead of being swallowed by that same
best-effort handling (analyze.py's ``except retry.ControlInterrupt: raise``
BEFORE ``except Exception``).
"""

from __future__ import annotations

import json

import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.events.log import record_event
from recon.findings import analyze
from recon.queue import retry
from recon.runs import service

pytestmark = pytest.mark.integration


def _run_with_signal(redis, tenant, session_id, signal: dict) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    # One asset so analyze has a unit to run; its bytes double as the scripts surface.
    key = storage.put_blob(tenant, view.id, "input", b"/*react*/ console.log(1)")
    with tenant_session(tenant) as session:
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=view.id,
                url="https://acme.io/app.js",
                input_ref=key,
                fetch_status="ok",
            )
        )
    signal_ref = storage.put_blob(
        tenant, view.id, "fingerprint-signal", json.dumps(signal).encode("utf-8")
    )
    with tenant_session(tenant) as session:
        record_event(
            session,
            tenant_id=tenant,
            run_id=view.id,
            event_type="fingerprint.signal",
            payload={"signal_ref": signal_ref, "hosts": 1},
        )
    return view.id


def _run_with_signal_no_assets(redis, tenant, session_id, signal: dict) -> str:
    """Same fingerprint-signal setup as ``_run_with_signal``, but with NO ``run_asset``
    row and no ``input_ref`` -- so ``analyze_run`` takes the ``_analyze_legacy`` branch,
    which returns an immediate ``Coverage(0, 0, 0)`` no-op and never calls
    ``run_queries.raise_if_control_requested`` (that check lives ONLY in
    ``_analyze_assets``'s per-asset loop). That makes the fingerprint pass's own
    control check the ONLY one this run ever exercises, so a raise from it can only
    be observed propagating out of ``techdetect_pass`` -- never mistaken for the
    unrelated, already-covered ``_analyze_assets`` cancel path
    (``test_analyze_loop_honors_cancel``)."""
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    signal_ref = storage.put_blob(
        tenant, view.id, "fingerprint-signal", json.dumps(signal).encode("utf-8")
    )
    with tenant_session(tenant) as session:
        record_event(
            session,
            tenant_id=tenant,
            run_id=view.id,
            event_type="fingerprint.signal",
            payload={"signal_ref": signal_ref, "hosts": len(signal)},
        )
    return view.id


def test_fingerprint_pass_writes_run_technology(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_signal(
        redis,
        tenant,
        session_id,
        {
            "acme.io": {
                "headers": {"server": "nginx/1.25.3"},
                "scripts": [],
                "meta": [],
                "cookies": [],
            }
        },
    )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)
    with tenant_session(tenant) as session:
        rows = session.query(models.RunTechnology).all()
    names = {r.name: r for r in rows}
    assert names["Nginx"].version == "1.25.3" and names["Nginx"].host == "acme.io"


def test_fingerprint_pass_is_idempotent_on_redelivery(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_signal(
        redis,
        tenant,
        session_id,
        {"acme.io": {"headers": {"server": "nginx"}, "scripts": [], "meta": [], "cookies": []}},
    )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)  # redeliver
    with tenant_session(tenant) as session:
        assert session.query(models.RunTechnology).filter_by(name="Nginx").count() == 1


def test_a_fingerprint_error_never_fails_the_run(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _run_with_signal(
        redis,
        tenant,
        session_id,
        {"acme.io": {"headers": {"server": "nginx"}, "scripts": [], "meta": [], "cookies": []}},
    )
    monkeypatch.setattr(
        "recon.findings.techdetect_pass.techdetect.detect",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # Must NOT raise -- best-effort (T2). Findings from the asset still land.
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)
    with tenant_session(tenant) as session:
        assert session.query(models.RunTechnology).count() == 0


def test_fingerprint_pass_control_interrupt_propagates_out_of_analyze_run(
    redis, authorized_session, monkeypatch
):
    # REQ-A4 regression guard for analyze.py:134-137's except ORDER: a
    # ControlInterrupt raised mid-fingerprint-pass is NOT a failure and must
    # propagate out of analyze_run uncaught -- never swallowed by the pass's
    # best-effort `except Exception` (T2, proven above). Genuinely mid-pass:
    # the fingerprint pass's OWN `raise_if_control_requested` call (imported in
    # techdetect_pass.py as `from recon.runs import queries as run_queries`) is
    # made to raise, and `_run_with_signal_no_assets` guarantees it is the ONLY
    # such call this run makes (see that helper's docstring) -- so this can only
    # go green because `techdetect_pass.run_fingerprint_pass` itself let the
    # interrupt through.
    tenant, session_id = authorized_session
    run_id = _run_with_signal_no_assets(
        redis,
        tenant,
        session_id,
        {"acme.io": {"headers": {"server": "nginx"}, "scripts": [], "meta": [], "cookies": []}},
    )
    calls: list[str] = []

    def fake_raise_if_control_requested(tenant_id: str, run_id: str) -> None:
        calls.append(run_id)
        raise retry.ControlInterrupt("cancel")

    monkeypatch.setattr(
        "recon.findings.techdetect_pass.run_queries.raise_if_control_requested",
        fake_raise_if_control_requested,
    )
    with pytest.raises(retry.ControlInterrupt) as ci:
        analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)
    assert ci.value.kind == "cancel"
    assert calls == [run_id]  # the fingerprint pass's own check actually fired
    # Never persisted -- the interrupt hit before the pass wrote anything.
    with tenant_session(tenant) as session:
        assert session.query(models.RunTechnology).count() == 0
