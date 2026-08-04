"""Slice Y multi-asset analyze loop — findings attributed + deduped across assets."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError
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


def test_analyze_loop_records_ok_and_failed_per_asset(redis, authorized_session, monkeypatch):
    # A genuine per-asset analyze failure (e.g. the extractor choking on one
    # asset's content) must be recorded as analyze_failed and must not abort the
    # run -- the good asset's finding still lands (best-effort, per-asset
    # commit). Triggered via extract() so this stays a genuine per-asset failure,
    # not an infra one (ClientError/SQLAlchemyError now propagate instead -- see
    # test_analyze_loop_reraises_infra_error_instead_of_recording_failed).
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id,
                        ["https://acme.io/a.js", "https://acme.io/bad.js"])
    rows = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    good_key = storage.put_blob(tenant, run_id, "input", b'fetch("/api/good");')
    bad_key = storage.put_blob(tenant, run_id, "input", b"BOOM_TRIGGER")
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows["https://acme.io/a.js"].id, good_key)
        assets.set_fetch_ok(s, rows["https://acme.io/bad.js"].id, bad_key)

    real_extract = analyze.extract

    def fake_extract(text, **kwargs):
        if text == "BOOM_TRIGGER":
            raise ValueError("simulated per-asset analyze failure")
        return real_extract(text, **kwargs)

    monkeypatch.setattr(analyze, "extract", fake_extract)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    result = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert result["https://acme.io/a.js"].analyze_status == "ok"
    assert result["https://acme.io/bad.js"].analyze_status == "failed"
    endpoint_values = {f.value for f in _findings(tenant, run_id) if f.type == "endpoint"}
    assert "GET /api/good" in endpoint_values


def test_analyze_loop_reraises_infra_error_instead_of_recording_failed(
    redis, authorized_session, monkeypatch
):
    # A transient infra error (S3 ClientError reading the blob, or a DB blip) must
    # propagate to the worker's job-level retry, not get recorded as a permanent
    # per-asset analyze_failed -- that would make a transient blip terminal (the
    # row becomes analyze-terminal, so redelivery's skip-condition never revisits
    # it) and could false-PARTIAL a run whose asset would have succeeded on retry.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    rows = assets.list_for_run(tenant, run_id)
    key = storage.put_blob(tenant, run_id, "input", b'fetch("/api/x");')
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows[0].id, key)

    def boom(*a, **k):
        raise ClientError({"Error": {"Code": "InternalError", "Message": "blip"}}, "GetObject")

    monkeypatch.setattr(storage, "get_blob", boom)
    with pytest.raises(ClientError):
        analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    # Never recorded as failed -- stays at its pre-attempt "pending", so the
    # worker's retry still sees an asset eligible for another analyze attempt.
    assert assets.list_for_run(tenant, run_id)[0].analyze_status == "pending"


def test_analyze_loop_does_not_overwrite_ok_when_publish_fails_post_commit(
    redis, authorized_session, monkeypatch
):
    # publish() runs AFTER the per-asset transaction has already committed
    # findings + analyze_status="ok". A failure there (Redis reset, pool
    # exhaustion, broker restart -- the DB itself perfectly healthy) must NOT
    # be recorded as an analyze failure: overwriting the just-committed "ok"
    # with "failed" would make the row self-contradictory (fully analyzed data,
    # "failed" status) and terminal (a redelivery's skip-condition would never
    # revisit it), silently finalizing the run PARTIAL over an asset that
    # actually succeeded, with no path back. Regression guard for the
    # try/except/else fix in _analyze_assets.
    tenant, session_id = authorized_session
    run_id = _crawl_run(redis, tenant, session_id, ["https://acme.io/a.js"])
    rows = assets.list_for_run(tenant, run_id)
    key = storage.put_blob(tenant, run_id, "input", b'fetch("/api/x");')
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, rows[0].id, key)

    def boom(*a, **k):
        raise RuntimeError("redis reset")

    monkeypatch.setattr(analyze, "publish", boom)
    with pytest.raises(RuntimeError, match="redis reset"):
        analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)

    # The finding was committed before publish ever ran...
    assert len(_findings(tenant, run_id)) == 1
    # ...and the status must still read "ok", NOT overwritten to "failed", and
    # the exception must have propagated (asserted above) so the worker's
    # normal job-level retry -- not a spurious per-asset failure -- handles it.
    assert assets.list_for_run(tenant, run_id)[0].analyze_status == "ok"


def test_merge_coverage_reports_less_healthy_secrets_engine():
    # Aggregating across assets must not let a LATER successful scan mask an
    # EARLIER asset going unscanned -- REQ-C2 honesty (see the docstring on
    # Coverage.secrets_engine: an absent scanner must not read as "no secrets").
    ok = analyze.Coverage(1, 0, 1, secrets=0, secrets_engine="ok")
    unavailable = analyze.Coverage(1, 0, 1, secrets=0, secrets_engine="unavailable")

    assert analyze._merge_coverage(unavailable, ok).secrets_engine == "unavailable"
    assert analyze._merge_coverage(ok, unavailable).secrets_engine == "unavailable"
    assert analyze._merge_coverage(ok, ok).secrets_engine == "ok"


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

    def fake_extract(text, **kwargs):
        calls.append(text)
        run_service.request_cancel(redis, tenant_id=tenant, run_id=run_id)
        return real_extract(text, **kwargs)

    monkeypatch.setattr(analyze, "extract", fake_extract)
    with pytest.raises(retry.ControlInterrupt) as ci:
        analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id, job_id=_JOB_ID)
    assert ci.value.kind == "cancel"
    assert len(calls) == 1  # asset 2 must never be analyzed

    result = {r.url: r for r in assets.list_for_run(tenant, run_id)}
    assert result["https://acme.io/a.js"].analyze_status == "ok"
    assert result["https://acme.io/b.js"].analyze_status == "pending"
