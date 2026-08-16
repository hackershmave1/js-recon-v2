"""Analyze's best-effort per-host tech-detection fingerprint pass (Task 8).

Writes ``run_technology`` from the run's ``fingerprint-signal`` blob (Tasks 6/7),
idempotently (T3), and must NEVER fail the run even when the detector itself
blows up (T2) -- the third test below is the load-bearing one.
"""

from __future__ import annotations

import json

import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.events.log import record_event
from recon.findings import analyze
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
