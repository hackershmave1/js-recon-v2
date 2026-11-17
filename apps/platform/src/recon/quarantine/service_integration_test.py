"""Quarantine end to end against Postgres and the blob store (ADR-0010): admit, caps, Add,
Ignore, compare-and-set, expiry, reference-counted delete, and Add after the capture ended."""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

import pytest
from sqlalchemy import select

from recon import storage
from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.quarantine import blobs, service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

HOST = "cdn.acme-static.com"
OTHER = "cdn.other-static.com"
JS = b"fetch('/api/orders');"


@pytest.fixture()
def world():
    tenant = sessions_service.create_tenant(f"quarantine-{uuid.uuid4().hex[:8]}")
    view = sessions_service.create_session(
        tenant, name="q", scope_hosts=["acme.io"], authorized_by="t"
    )
    return tenant, view.id, _run(tenant, view.id, "capturing")


def _run(tenant: str, session_id: str, state: str) -> str:
    with tenant_session(tenant) as session:
        # A capturing run with no marker is "sealed" to the worker's sweeper, which settles it
        # across tenants and would disturb the sweep tests that share this database.
        marker = f"ext-{uuid.uuid4().hex}" if state == "capturing" else None
        run = models.Run(
            tenant_id=tenant, session_id=session_id, state=state, capture_external_id=marker
        )
        session.add(run)
        session.flush()
        return str(run.id)


def _admit(world, host=HOST, files=None, run_id=None):
    tenant, session_id, default_run = world
    files = files if files is not None else [service.FileIn(f"https://{host}/app.js", JS)]
    return service.admit(
        tenant_id=tenant,
        session_id=session_id,
        run_id=run_id or default_run,
        host=host,
        files=files,
    )


def _assets(tenant, session_id, host=None):
    with tenant_session(tenant) as session:
        q = select(models.QuarantinedAsset).where(models.QuarantinedAsset.session_id == session_id)
        if host:
            q = q.where(models.QuarantinedAsset.host == host)
        return session.scalars(q).all()


def _run_assets(tenant, run_id):
    with tenant_session(tenant) as session:
        return session.scalars(
            select(models.RunAsset).where(models.RunAsset.run_id == run_id)
        ).all()


def _state(tenant, session_id, host):
    with tenant_session(tenant) as session:
        return session.scalar(
            select(models.HostDecision.state).where(
                models.HostDecision.session_id == session_id, models.HostDecision.host == host
            )
        )


def _blob_exists(key: str) -> bool:
    return key in storage.list_keys(key)


def _settings_with(monkeypatch, **overrides):
    patched = get_settings().model_copy(update=overrides)
    monkeypatch.setattr(service, "get_settings", lambda: patched)


# --- admit: stored apart ---------------------------------------------------------------------- #


def test_admit_stores_apart_from_the_runs_assets(world):
    tenant, session_id, run_id = world
    [result] = _admit(world)
    assert result.outcome == service.STORED
    [asset] = _assets(tenant, session_id)
    assert asset.content_ref == f"{tenant}/{run_id}/quarantine/{hashlib.sha256(JS).hexdigest()}"
    assert _blob_exists(asset.content_ref)
    assert _run_assets(tenant, run_id) == []  # never a run_asset, so no reader of those sees it
    assert _state(tenant, session_id, HOST) == "undecided"


def test_a_retried_batch_is_a_no_op(world):
    _admit(world)
    [again] = _admit(world)
    assert again.outcome == service.DUPLICATE
    assert len(_assets(world[0], world[1])) == 1


def test_empty_and_oversized_files_are_invalid(world, monkeypatch):
    _settings_with(monkeypatch, max_upload_bytes=10)
    results = _admit(
        world,
        files=[service.FileIn("https://x/a.js", b""), service.FileIn("https://x/b.js", b"x" * 11)],
    )
    assert [r.outcome for r in results] == [service.INVALID, service.INVALID]


def test_a_run_of_another_session_is_refused(world):
    tenant, session_id, _ = world
    other = sessions_service.create_session(tenant, name="o", scope_hosts=[], authorized_by="t")
    foreign_run = _run(tenant, other.id, "capturing")
    with pytest.raises(service.QuarantineError):
        service.admit(
            tenant_id=tenant,
            session_id=session_id,
            run_id=foreign_run,
            host=HOST,
            files=[service.FileIn("https://x/a.js", JS)],
        )


# --- caps ------------------------------------------------------------------------------------- #


def test_per_host_file_and_byte_caps(world, monkeypatch):
    _settings_with(monkeypatch, quarantine_max_files_per_host=2, quarantine_max_bytes_per_host=25)
    files = [service.FileIn(f"https://{HOST}/{i}.js", b"a" * 10 + bytes([i])) for i in range(4)]
    outcomes = [r.outcome for r in _admit(world, files=files)]
    assert outcomes == [
        service.STORED,
        service.STORED,
        service.CAP_HOST_FILES,
        service.CAP_HOST_FILES,
    ]
    _settings_with(monkeypatch, quarantine_max_files_per_host=10, quarantine_max_bytes_per_host=25)
    [big] = _admit(world, files=[service.FileIn(f"https://{HOST}/big.js", b"z" * 9)])
    assert big.outcome == service.CAP_HOST_BYTES  # 22 already stored, 9 more exceeds 25


def test_per_session_byte_cap_and_host_count_cap(world, monkeypatch):
    _settings_with(monkeypatch, quarantine_max_bytes_per_session=15)
    stored = _admit(world, files=[service.FileIn("https://a/1.js", b"x" * 10)])
    assert stored[0].outcome == service.STORED
    over = _admit(world, host=OTHER, files=[service.FileIn("https://b/2.js", b"y" * 10)])
    assert over[0].outcome == service.CAP_SESSION_BYTES
    _settings_with(
        monkeypatch, quarantine_max_hosts_per_session=1, quarantine_max_bytes_per_session=10**9
    )
    third = _admit(world, host="third.example", files=[service.FileIn("https://c/3.js", b"q")])
    assert third[0].outcome == service.CAP_HOSTS


# --- Ignore ----------------------------------------------------------------------------------- #


def test_ignore_deletes_the_content_and_refuses_a_replay(world):
    tenant, session_id, _ = world
    _admit(world)
    [asset] = _assets(tenant, session_id)
    result = service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="ignore")
    assert (result.status, result.state, result.deleted) == ("ok", "ignored", 1)
    assert _assets(tenant, session_id) == [] and not _blob_exists(asset.content_ref)
    assert service.ignored_hosts(tenant_id=tenant, session_id=session_id) == [HOST]
    # A replayed batch after Ignore cannot re-store.
    [replay] = _admit(world)
    assert replay.outcome == service.IGNORED
    assert _assets(tenant, session_id) == []


# --- Add -------------------------------------------------------------------------------------- #


def test_add_widens_scope_and_promotes_into_normal_run_assets(world):
    tenant, session_id, run_id = world
    _admit(world)
    [asset] = _assets(tenant, session_id)
    result = service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="add")
    assert (result.status, result.state, result.promoted) == ("ok", "added", 1)
    assert result.analysis == {run_id: "scheduled"}  # the run is still open: a pass is scheduled
    [row] = _run_assets(tenant, run_id)
    assert row.url == f"https://{HOST}/app.js" and row.fetch_status == "ok"
    assert row.input_ref == f"{tenant}/{run_id}/input/{hashlib.sha256(JS).hexdigest()}"
    assert storage.get_blob(row.input_ref) == JS
    assert _assets(tenant, session_id) == [] and not _blob_exists(asset.content_ref)
    with tenant_session(tenant) as session:
        scope = session.scalar(
            select(models.EngagementSession.scope_hosts).where(
                models.EngagementSession.id == session_id
            )
        )
    assert HOST in scope and "acme.io" in scope
    # Content from the added host after Add goes the normal capture path.
    assert _admit(world)[0].outcome == service.IN_SCOPE


def _session_runs(tenant, session_id):
    with tenant_session(tenant) as session:
        return list(
            session.scalars(select(models.Run).where(models.Run.session_id == session_id)).all()
        )


def test_add_after_the_capture_ended_goes_to_a_followup_run_that_is_analyzed_on_its_own(
    world, redis
):
    from recon.runs import capture_pass

    tenant, session_id, _ = world
    done = _run(tenant, session_id, "done")
    _admit(world, run_id=done)
    result = service.decide(
        tenant_id=tenant, session_id=session_id, host=HOST, decision="add", redis=redis
    )
    assert result.promoted == 1
    outcome = result.analysis[done]
    assert outcome.startswith("followup:")
    followup = outcome.split(":", 1)[1]
    # A finished run is an immutable snapshot: it is untouched, the scripts live in a new run of the
    # same session, which is sealed and already has its analysis pass queued (no button to press).
    assert _run_assets(tenant, done) == []
    assert [a.url for a in _run_assets(tenant, followup)] == [f"https://{HOST}/app.js"]
    with tenant_session(tenant) as session:
        run = session.get(models.Run, followup)
        assert run.capture_external_id is None and str(run.session_id) == session_id
        assert capture_pass.has_job(session, followup)
    assert (
        len(_session_runs(tenant, session_id)) == 3
    )  # the open capture, the finished one, the follow-up


def test_a_followup_that_cannot_start_is_reported_not_an_error(world):
    tenant, session_id, _ = world
    done = _run(tenant, session_id, "done")
    _admit(world, run_id=done)
    result = service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="add")
    assert result.promoted == 1 and result.analysis == {done: "needs_rerun"}


# --- compare-and-set -------------------------------------------------------------------------- #


def test_one_decision_wins_and_the_same_decision_is_idempotent(world):
    tenant, session_id, _ = world
    _admit(world)
    first = service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="add")
    again = service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="add")
    other = service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="ignore")
    assert first.status == "ok" and again.status == "ok" and again.promoted == 0
    assert (other.status, other.state) == ("conflict", "added")
    missing = service.decide(
        tenant_id=tenant, session_id=session_id, host="nope.example", decision="add"
    )
    assert missing.status == "not_found"


# --- expiry ----------------------------------------------------------------------------------- #


def test_sweep_expires_undecided_hosts_and_deletes_their_content(world):
    tenant, session_id, _ = world
    _admit(world)
    [asset] = _assets(tenant, session_id)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=8)
    assert service.sweep(now=later) >= 1
    assert _state(tenant, session_id, HOST) == "expired"
    assert _assets(tenant, session_id) == [] and not _blob_exists(asset.content_ref)
    # Add after the 7-day sweep reports expired (a conflict naming the state), never promotes.
    late_add = service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="add")
    assert (late_add.status, late_add.state) == ("conflict", "expired")
    # New content from an expired host starts a fresh undecided cycle.
    assert _admit(world)[0].outcome == service.STORED
    assert _state(tenant, session_id, HOST) == "undecided"


def test_sweep_leaves_a_decided_host_alone(world):
    tenant, session_id, _ = world
    _admit(world)
    service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="ignore")
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
    service.sweep(now=later)
    assert _state(tenant, session_id, HOST) == "ignored"


# --- reference-counted delete ------------------------------------------------------------------ #


def test_identical_bytes_from_two_hosts_share_one_blob_until_the_last_is_deleted(world):
    tenant, session_id, _ = world
    _admit(world, host=HOST)
    _admit(world, host=OTHER, files=[service.FileIn(f"https://{OTHER}/app.js", JS)])
    keys = {a.content_ref for a in _assets(tenant, session_id)}
    assert len(keys) == 1  # content-addressed within the run: one object, two rows
    [key] = keys
    service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="ignore")
    assert _blob_exists(key)  # still referenced by the other host
    service.decide(tenant_id=tenant, session_id=session_id, host=OTHER, decision="ignore")
    assert not _blob_exists(key)


def test_a_run_asset_with_the_same_bytes_does_not_keep_the_quarantine_blob_alive(world):
    tenant, session_id, run_id = world
    digest = hashlib.sha256(JS).hexdigest()
    input_key = storage.put_blob(tenant, run_id, "input", JS)
    with tenant_session(tenant) as session:
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=run_id,
                url="https://acme.io/x.js",
                input_ref=input_key,
                fetch_status="ok",
            )
        )
    _admit(world)
    service.decide(tenant_id=tenant, session_id=session_id, host=HOST, decision="ignore")
    assert not _blob_exists(f"{tenant}/{run_id}/quarantine/{digest}")
    assert _blob_exists(input_key)  # the run's own asset is untouched


def test_deleting_an_asset_keeps_shared_bytes_and_removes_derivatives_when_last(world):
    tenant, _, run_id = world
    digest = hashlib.sha256(JS).hexdigest()
    storage.put_blob(tenant, run_id, "input", JS)
    for kind in ("formatted", "originals"):
        storage.put_blob_at(f"{tenant}/{run_id}/{kind}/{digest}", b"derived")
    map_key = storage.put_blob(tenant, run_id, "source_map", b"{}")
    input_key = f"{tenant}/{run_id}/input/{digest}"
    with tenant_session(tenant) as session:
        for name in ("a", "b"):
            session.add(
                models.RunAsset(
                    tenant_id=tenant,
                    run_id=run_id,
                    url=f"https://acme.io/{name}.js",
                    input_ref=input_key,
                    source_map_ref=map_key,
                    fetch_status="ok",
                )
            )
    with tenant_session(tenant) as session:
        session.query(models.RunAsset).filter_by(run_id=run_id, url="https://acme.io/a.js").delete()
        shared = blobs.unreferenced_keys(
            session, tenant_id=tenant, run_id=run_id, input_refs=[input_key], map_refs=[map_key]
        )
        assert shared == []  # asset b still references the digest and its map
        session.query(models.RunAsset).filter_by(run_id=run_id, url="https://acme.io/b.js").delete()
        last = blobs.unreferenced_keys(
            session, tenant_id=tenant, run_id=run_id, input_refs=[input_key], map_refs=[map_key]
        )
    assert set(last) == {
        input_key,
        f"{tenant}/{run_id}/formatted/{digest}",
        f"{tenant}/{run_id}/originals/{digest}",
        map_key,
    }
