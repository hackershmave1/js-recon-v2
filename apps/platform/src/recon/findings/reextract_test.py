"""Integration tests for the out-of-band wrapper re-extract (spec §6).

Requires the full compose stack (Postgres, Redis, MinIO): a re-extract re-reads a
run's stored source blob(s) and records wrapper endpoints through the idempotent
outbox, without re-emitting coverage or transitioning run state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import AssetStatus
from recon.findings import analyze, queries, reextract
from recon.findings.normalize import finding_hash
from recon.findings.wrappers import WrapperRule
from recon.runs import service

pytestmark = pytest.mark.integration


def _seed_single(redis, tenant, session_id, source: bytes) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", source)
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run).where(models.Run.id == view.id).values(input_ref=key)
        )
    return view.id


def _seed_crawl(redis, tenant, session_id, blobs: dict[str, bytes]) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    with tenant_session(tenant) as session:
        for url, src in blobs.items():
            key = storage.put_blob(tenant, view.id, "input", src)
            session.add(models.RunAsset(
                tenant_id=tenant, run_id=view.id, url=url, input_ref=key,
                fetch_status=AssetStatus.OK.value, analyze_status=AssetStatus.PENDING.value,
            ))
    return view.id


def _endpoint_findings(tenant, run_id) -> dict[str, models.Finding]:
    with tenant_session(tenant) as session:
        rows = session.execute(
            select(models.Finding).where(
                models.Finding.run_id == run_id, models.Finding.type == "endpoint",
            )
        ).scalars().all()
        return {r.value: r for r in rows}


def _coverage_event_count(tenant, run_id) -> int:
    with tenant_session(tenant) as session:
        return len(session.execute(
            select(models.RunEvent.id).where(
                models.RunEvent.run_id == run_id,
                models.RunEvent.type == "analyze.coverage",
            )
        ).all())


def test_reextract_recovers_wrapper_endpoint(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_single(redis, tenant, session_id, b"const api = makeClient(); api.get('/users');")

    written = reextract.reextract_run(tenant, run_id, [WrapperRule("api")])

    assert written >= 1
    found = _endpoint_findings(tenant, run_id)
    assert "GET /users" in found
    assert found["GET /users"].attributes["wrapper"] == "api"
    assert found["GET /users"].attributes["kind"] == "axios"
    # Reaches the downstream read path classify/probe/export consume.
    listed = queries.list_findings(tenant, run_id)
    assert any(f.value == "GET /users" for f in listed.findings)


def test_reextract_preserves_native_hashes_and_adds_wrapper(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_single(
        redis, tenant, session_id,
        b"fetch('/native'); const api = makeClient(); api.get('/w');",
    )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)  # natives recorded
    before = set(_endpoint_findings(tenant, run_id))
    assert "GET /native" in before and "GET /w" not in before

    reextract.reextract_run(tenant, run_id, [WrapperRule("api")])

    after = set(_endpoint_findings(tenant, run_id))
    assert before <= after  # native endpoints not churned (§12 Imp 4)
    assert after - before == {"GET /w"}
    # The native finding_hash is exactly the pre-wrapper identity (path input.js).
    native = _endpoint_findings(tenant, run_id)["GET /native"]
    assert native.finding_hash == finding_hash("endpoint", "GET /native", "input.js")


def test_reextract_is_idempotent(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_single(redis, tenant, session_id, b"const api = makeClient(); api.get('/u');")

    first = reextract.reextract_run(tenant, run_id, [WrapperRule("api")])
    values1 = set(_endpoint_findings(tenant, run_id))
    second = reextract.reextract_run(tenant, run_id, [WrapperRule("api")])
    values2 = set(_endpoint_findings(tenant, run_id))

    assert first >= 1 and second == 0  # re-run writes nothing new (outbox no-op)
    assert values1 == values2


def test_reextract_multi_asset_does_not_reemit_coverage(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_crawl(
        redis, tenant, session_id,
        {"https://acme.io/a.js": b"const api = makeClient(); api.get('/a');"},
    )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)  # one asset -> one coverage event
    before = _coverage_event_count(tenant, run_id)

    reextract.reextract_run(tenant, run_id, [WrapperRule("api")])

    assert _coverage_event_count(tenant, run_id) == before  # no coverage double-count (§12 Blocker 1)
    assert "GET /a" in _endpoint_findings(tenant, run_id)


def test_reextract_multi_asset_preserves_capture_map_path(redis, authorized_session, monkeypatch):
    # §4 MUST-FIX regression: a capture asset's ORIGINAL findings are attributed to the
    # map-recovered path, so re-extract must recover the SAME path — else the wrapper
    # endpoint lands under `input.js` with a divergent finding_hash (a duplicate, not
    # an update; §12 Imp 4). Before the fix, reextract hardcoded source_map_ref=None
    # for assets. recover_sources is faked (no Go binary); the recovered source carries
    # the wrapper call.
    from recon.findings import sourcemapper

    tenant, session_id = authorized_session
    recovered_src = b"const api = makeClient(); api.get('/w');"

    def fake_recover(map_bytes, **_kwargs):
        return sourcemapper.RecoveredSources(
            files=[sourcemapper.RecoveredFile("app/src/api.js", recovered_src)],
            status="ok", origin="capture",
        )

    monkeypatch.setattr(sourcemapper, "recover_sources", fake_recover)

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", recovered_src)
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.add(models.RunAsset(
            tenant_id=tenant, run_id=view.id, url="https://acme.io/app.js",
            input_ref=input_key, source_map_ref=map_key,
            fetch_status=AssetStatus.OK.value, analyze_status=AssetStatus.OK.value,
        ))

    reextract.reextract_run(tenant, view.id, [WrapperRule("api")])

    found = _endpoint_findings(tenant, view.id)
    assert "GET /w" in found
    assert found["GET /w"].path == "app/src/api.js"  # recovered path, NOT input.js
    assert found["GET /w"].finding_hash == finding_hash("endpoint", "GET /w", "app/src/api.js")


def test_reextract_unknown_run_is_none(redis, authorized_session):
    tenant, _session_id = authorized_session
    assert reextract.reextract_run(
        tenant, "00000000-0000-0000-0000-000000000000", [WrapperRule("api")]
    ) is None


def test_reextract_missing_blob_raises(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run).where(models.Run.id == view.id)
            .values(input_ref=f"{tenant}/{view.id}/input/deadbeef")  # no such object
        )
    with pytest.raises(reextract.SourceBlobMissing):
        reextract.reextract_run(tenant, view.id, [WrapperRule("api")])
