"""Slice Y multi-asset analyze loop — findings attributed + deduped across assets."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze
from recon.queue import retry
from recon.runs import assets, service

pytestmark = pytest.mark.integration

# Per-asset heartbeats write to a real `job` row keyed by this id (UUID column) —
# it must be well-formed even though no Job row exists for it in these tests
# (heartbeat.beat's UPDATE then just matches zero rows, which is not an error).
_JOB_ID = "11111111-1111-1111-1111-111111111111"


def _crawl_run(redis, tenant, session_id, urls):
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=view.id, urls=urls)
    return view.id


def _findings(tenant, run_id):
    with tenant_session(tenant) as s:
        return list(
            s.execute(select(models.Finding).where(models.Finding.run_id == run_id)).scalars()
        )


def test_analyze_loop_dedups_across_assets_with_attribution(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    run_id = view.id
    # Two assets, both calling the same endpoint -> one finding, two occurrences.
    src = b'fetch("/api/shared");'
    k1 = storage.put_blob(tenant, run_id, "input", src)
    k2 = storage.put_blob(tenant, run_id, "input", src + b" ")  # distinct bytes -> distinct key
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id,
                            urls=["https://acme.io/a.js", "https://acme.io/b.js"])
    rows = assets.list_for_run(tenant, run_id)
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows[0].id, k1)
        assets.set_fetch_ok(s, rows[1].id, k2)

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    with tenant_session(tenant) as s:
        findings = s.execute(select(models.Finding).where(models.Finding.run_id == run_id)
                             ).scalars().all()
        endpoint = [f for f in findings if f.type == "endpoint"]
        assert len(endpoint) == 1
        occ = s.execute(
            select(models.FindingOccurrence)
            .where(models.FindingOccurrence.finding_id == endpoint[0].id)
        ).scalars().all()
        assert {str(o.run_asset_id) for o in occ} == {rows[0].id, rows[1].id}
    assert all(a.analyze_status == "ok" for a in assets.list_for_run(tenant, run_id))


def test_analyze_loop_records_ok_and_failed_per_asset(redis, authorized_session):
    # The "bad" asset's blob key was never actually stored, so storage.get_blob
    # raises for real inside _analyze_blob (no monkeypatching kingfisher/extract
    # needed) -- the good asset's finding must still land despite the other's
    # failure (best-effort, per-asset commit).
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id,
                        ["https://acme.io/a.js", "https://acme.io/bad.js"])
    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    good_key = storage.put_blob(tenant, run_id, "input", b'fetch("/api/good");')
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows["https://acme.io/a.js"].id, good_key)
        assets.set_fetch_ok(s, rows["https://acme.io/bad.js"].id, "tenant/run/input/does-not-exist")

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    result = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert result["https://acme.io/a.js"].analyze_status == "ok"
    assert result["https://acme.io/bad.js"].analyze_status == "failed"
    endpoint_values = {f.value for f in _findings(tenant, run_id) if f.type == "endpoint"}
    assert "GET /api/good" in endpoint_values


def test_analyze_loop_is_idempotent_on_redelivery(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    rows = assets.list_for_run(tenant, run_id)
    key = storage.put_blob(tenant, run_id, "input", b'fetch("/api/once");')
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows[0].id, key)

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)
    assert assets.list_for_run(tenant, run_id)[0].analyze_status == "ok"

    def _must_not_analyze(*a, **k):
        raise AssertionError("re-analyzed a terminal asset")

    monkeypatch.setattr(analyze, "extract", _must_not_analyze)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)  # no-op

    assert len(_findings(tenant, run_id)) == 1  # not double-recorded


def test_analyze_loop_honors_cancel(redis, authorized_session, monkeypatch):
    # Genuinely mid-loop: cancel arrives WHILE asset 1 is "in flight" (extract()
    # itself requests it, as another actor would), not before the loop starts --
    # so this guards against a future refactor hoisting the control check out of
    # the loop body.
    from recon.runs import service as run_service

    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id,
                        ["https://acme.io/a.js", "https://acme.io/b.js"])
    key = storage.put_blob(tenant, run_id, "input", b'fetch("/api/x");')
    rows = assets.list_for_run(tenant, run_id)
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows[0].id, key)
        assets.set_fetch_ok(s, rows[1].id, key)

    real_extract = analyze.extract
    calls = []

    def fake_extract(text):
        calls.append(text)
        run_service.request_cancel(redis, tenant_id=tenant, run_id=run_id)
        return real_extract(text)

    monkeypatch.setattr(analyze, "extract", fake_extract)
    with pytest.raises(retry.ControlInterrupt) as ci:
        analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)
    assert ci.value.kind == "cancel"
    assert len(calls) == 1  # asset 2 must never be analyzed

    result = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert result["https://acme.io/a.js"].analyze_status == "ok"
    assert result["https://acme.io/b.js"].analyze_status == "pending"
