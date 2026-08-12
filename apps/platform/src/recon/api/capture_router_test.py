"""Integration tests for the flag-gated extension -> platform ingest (Phase 1).

Proves the "run-per-capture-session" model end to end: batches accumulate into one
QUEUED run (idempotently, no worker), then analyze/start emits the discover.assets
event + enqueues one walk that the REAL worker drives to findings — with the
pre-ANALYZING stages no-op'ing over the pre-fetched assets (no katana, no network).

Needs the live stack (Postgres/Redis/MinIO) like the other integration tests; the
S3 endpoint is taken from the environment (RECON_S3_ENDPOINT_URL -> MinIO).
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from recon import storage
from recon.api import capture_router
from recon.api.app import create_app
from recon.config import get_settings
from recon.db.base import admin_session, tenant_session
from recon.db.models import EngagementSession, Job, Run, Tenant
from recon.discover import queries as discover_queries
from recon.domain import RunState
from recon.fetch import egress, fetch
from recon.findings import analyze as analyze_mod
from recon.findings import queries as findings_queries
from recon.pairing import token as pairing_token
from recon.queue import retry
from recon.runs import assets as run_assets
from recon.runs import queries as run_queries
from recon.runs import state_machine as sm
from recon.sessions import service as sessions_service
from recon.worker import main as worker

pytestmark = pytest.mark.integration


@pytest.fixture()
def make_capture_client(monkeypatch, redis):
    """Build a TestClient with the capture-ingest flag ON and a UNIQUE capture
    tenant per test (so tests don't collide on a shared tenant). Extra kwargs set
    additional ``RECON_*`` env before the settings cache is rebuilt."""

    def _make(**env) -> TestClient:
        name = f"capture-test-{uuid.uuid4().hex[:8]}"
        monkeypatch.setenv("RECON_ENABLE_CAPTURE_INGEST", "true")
        monkeypatch.setenv("RECON_CAPTURE_TENANT_NAME", name)
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()
        client = TestClient(create_app())
        client.capture_tenant_name = name  # type: ignore[attr-defined]
        return client

    yield _make
    get_settings.cache_clear()


def _tenant_id(name: str) -> str:
    with admin_session() as session:
        return str(session.scalar(select(Tenant.id).where(Tenant.name == name)))


def _file(url: str, content: str, session_id: str) -> dict:
    return {
        "url": url,
        "content": content,
        "sessionId": session_id,
        "contentHash": hashlib.sha256(content.encode()).hexdigest()[:16],
        "contentLength": len(content),
    }


def _save(client: TestClient, sid: str, files: list[dict]) -> dict:
    r = client.post(
        "/api/save-files",
        json={"metadata": {"sessionId": sid, "disableAnalysis": True}, "files": files},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# save-files: accumulate, idempotency, per-file failure isolation.
# --------------------------------------------------------------------------- #


def test_health(make_capture_client):
    r = make_capture_client().get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# --------------------------------------------------------------------------- #
# Origin-lock: state-changing ingest POSTs reject a web-page Origin (anti-CSRF).
# The guard logic is unit-tested hermetically in capture_origin_lock_test.py;
# these prove it is actually wired into each real endpoint (and the kill-switch).
# --------------------------------------------------------------------------- #


def test_origin_lock_rejects_web_origin_on_save_files(make_capture_client):
    r = make_capture_client().post(
        "/api/save-files",
        json={"metadata": {}, "files": []},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_origin_lock_rejects_web_origin_on_analyze_start(make_capture_client):
    r = make_capture_client().post(
        "/api/sessions/whatever/analyze/start",
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403  # rejected before the unknown-session 404


def test_origin_lock_rejects_web_origin_on_create_project(make_capture_client):
    r = make_capture_client().post(
        "/api/projects",
        json={"name": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_origin_lock_allows_extension_origin(make_capture_client):
    # chrome-extension:// is not http(s) → passes the guard; an empty batch then
    # returns 200 (the extension's real Origin, and the happy path).
    r = make_capture_client().post(
        "/api/save-files",
        json={"metadata": {}, "files": []},
        headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"},
    )
    assert r.status_code == 200


def test_origin_lock_allows_when_no_origin(make_capture_client):
    r = make_capture_client().post("/api/save-files", json={"metadata": {}, "files": []})
    assert r.status_code == 200


def test_origin_lock_kill_switch_allows_web_origin(make_capture_client):
    client = make_capture_client(RECON_CAPTURE_INGEST_ORIGIN_LOCK="false")
    r = client.post(
        "/api/save-files",
        json={"metadata": {}, "files": []},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Pairing: a valid Bearer token re-homes ingest into the operator tenant; an
# absent/invalid token falls back (paired flag) to the shared capture tenant.
# --------------------------------------------------------------------------- #

_PAIRING_KEY = "test-pairing-key"


def _make_operator_tenant() -> str:
    with admin_session() as session:
        tenant = Tenant(name=f"op-{uuid.uuid4().hex[:8]}")
        session.add(tenant)
        session.flush()
        return str(tenant.id)


def _one_file_batch(sid: str) -> dict:
    return {
        "metadata": {"sessionId": sid, "disableAnalysis": True},
        "files": [_file("https://acme.io/a.js", "fetch('/x');", sid)],
    }


def test_save_files_with_valid_bearer_lands_in_operator_tenant(make_capture_client):
    client = make_capture_client(RECON_PAIRING_KEY=_PAIRING_KEY)
    op_tenant = _make_operator_tenant()
    token = pairing_token.mint(op_tenant, key=_PAIRING_KEY, ttl_seconds=3600)
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/save-files", json=_one_file_batch(sid), headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["paired"] is True
    # Session created under the OPERATOR tenant, never the shared capture-spike tenant.
    assert capture_router._find_session_by_external_id(op_tenant, sid) is not None
    spike = _tenant_id(client.capture_tenant_name)
    assert capture_router._find_session_by_external_id(spike, sid) is None


def test_save_files_without_bearer_is_unpaired_and_shared(make_capture_client):
    client = make_capture_client(RECON_PAIRING_KEY=_PAIRING_KEY)
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/save-files", json=_one_file_batch(sid))
    assert r.status_code == 200
    assert r.json()["paired"] is False
    spike = _tenant_id(client.capture_tenant_name)
    assert capture_router._find_session_by_external_id(spike, sid) is not None


def test_save_files_with_invalid_bearer_falls_back_closed(make_capture_client):
    client = make_capture_client(RECON_PAIRING_KEY=_PAIRING_KEY)
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/save-files",
        json=_one_file_batch(sid),
        headers={"Authorization": "Bearer garbage.token"},
    )
    assert r.status_code == 200
    assert r.json()["paired"] is False
    spike = _tenant_id(client.capture_tenant_name)
    assert capture_router._find_session_by_external_id(spike, sid) is not None


def test_bearer_rehomes_all_endpoints_not_just_save_files(make_capture_client):
    # The §4 must-fix: progress must resolve the SAME tenant as save-files, or a paired
    # popup 404s. Save under the operator tenant with the token, then progress WITH the
    # token finds the session (200), WITHOUT it (capture-spike) does not (404).
    client = make_capture_client(RECON_PAIRING_KEY=_PAIRING_KEY)
    op_tenant = _make_operator_tenant()
    token = pairing_token.mint(op_tenant, key=_PAIRING_KEY, ttl_seconds=3600)
    auth = {"Authorization": f"Bearer {token}"}
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    assert (
        client.post("/api/save-files", json=_one_file_batch(sid), headers=auth).status_code == 200
    )
    assert client.get(f"/api/sessions/{sid}/analyze/progress", headers=auth).status_code == 200
    assert client.get(f"/api/sessions/{sid}/analyze/progress").status_code == 404


def test_save_files_accumulates_one_queued_run(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    body = _save(
        client,
        sid,
        [
            _file("https://acme.io/a.js", "fetch('/api/v1/users');", sid),
            _file("https://acme.io/b.js", "fetch('/v2/orders',{method:'POST'});", sid),
        ],
    )
    assert body["stored"] == 2 and body["failed"] == 0
    run_id, tid = body["runId"], _tenant_id(client.capture_tenant_name)

    with tenant_session(tid) as s:
        run = s.get(Run, run_id)
        assert run.state == RunState.QUEUED.value
        # multi-asset: content lives on run_asset.input_ref, NEVER run.input_ref
        # (setting it would put the run on the singular-upload path). target stays
        # None so the discover/fetch stages no-op (never crawl/egress).
        assert run.input_ref is None
        assert run.target is None
        n_jobs = s.scalar(select(func.count()).select_from(Job).where(Job.run_id == run_id))
        assert n_jobs == 0  # not enqueued until analyze/start

    rows = run_assets.list_for_run(tid, run_id)
    assert len(rows) == 2
    assert all(row.fetch_status == "ok" and row.input_ref for row in rows)
    assert len(findings_queries.list_findings(tid, run_id).findings) == 0  # no analysis yet


def test_save_files_retry_is_idempotent(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    files = [_file("https://acme.io/a.js", "a();", sid), _file("https://acme.io/b.js", "b();", sid)]
    b1 = _save(client, sid, files)
    b2 = _save(client, sid, files)  # exact retry (same bytes, same urls)
    assert b1["runId"] == b2["runId"]  # reused the accumulating run
    tid = _tenant_id(client.capture_tenant_name)
    assert len(run_assets.list_for_run(tid, b1["runId"])) == 2  # no duplicate assets
    with tenant_session(tid) as s:
        n_runs = s.scalar(
            select(func.count()).select_from(Run).where(Run.session_id == b1["sessionId"])
        )
        assert n_runs == 1  # no duplicate run


def test_two_batches_accumulate_into_one_run(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    b1 = _save(client, sid, [_file("https://acme.io/a.js", "a();", sid)])
    b2 = _save(client, sid, [_file("https://acme.io/b.js", "b();", sid)])
    assert b1["runId"] == b2["runId"]
    tid = _tenant_id(client.capture_tenant_name)
    rows = run_assets.list_for_run(tid, b1["runId"])
    assert {row.url for row in rows} == {"https://acme.io/a.js", "https://acme.io/b.js"}


def test_duplicate_url_in_batch_first_wins(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    first, second = "first();", "second();"
    body = _save(
        client,
        sid,
        [
            _file("https://acme.io/x.js", first, sid),
            _file("https://acme.io/x.js", second, sid),  # same url, different content
        ],
    )
    tid = _tenant_id(client.capture_tenant_name)
    rows = run_assets.list_for_run(tid, body["runId"])
    assert len(rows) == 1  # coalesced to a single (run_id, url) asset
    assert rows[0].input_ref.endswith(hashlib.sha256(first.encode()).hexdigest())  # first wins


def test_oversize_file_is_a_per_file_failure_not_a_batch_drop(make_capture_client):
    client = make_capture_client(RECON_MAX_UPLOAD_BYTES=16)
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    body = _save(
        client,
        sid,
        [
            _file("https://acme.io/big.js", "x" * 100, sid),  # over the 16-byte cap
            _file("https://acme.io/ok.js", "y();", sid),
        ],
    )
    assert body["stored"] == 1 and body["failed"] == 1  # batch survived, sibling stored
    tid = _tenant_id(client.capture_tenant_name)
    rows = run_assets.list_for_run(tid, body["runId"])
    assert {row.url for row in rows} == {"https://acme.io/ok.js"}


def test_malformed_file_does_not_reject_the_whole_batch(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    body = _save(
        client,
        sid,
        [
            {"url": "https://acme.io/bad.js"},  # missing content -> per-file failure, not a 422
            _file("https://acme.io/good.js", "ok();", sid),
        ],
    )
    assert body["stored"] == 1 and body["failed"] == 1
    tid = _tenant_id(client.capture_tenant_name)
    assert {r.url for r in run_assets.list_for_run(tid, body["runId"])} == {
        "https://acme.io/good.js"
    }


def test_save_files_without_session_id_is_noop(make_capture_client):
    r = make_capture_client().post("/api/save-files", json={"metadata": {}, "files": []})
    assert r.status_code == 200 and r.json()["runId"] is None and r.json()["stored"] == 0


def test_blob_store_failure_returns_503_so_the_extension_retries(make_capture_client, monkeypatch):
    # An infra failure (object store down) must be a 5xx: the extension treats any
    # non-429 4xx as a PERMANENT drop of un-recapturable JS, but retries 5xx.
    client = make_capture_client()
    monkeypatch.setattr(
        "recon.storage.put_blob", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("minio down"))
    )
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/save-files",
        json={
            "metadata": {"sessionId": sid},
            "files": [_file("https://acme.io/a.js", "a();", sid)],
        },
    )
    assert r.status_code == 503


# --------------------------------------------------------------------------- #
# analyze/start: emit + enqueue once, idempotent, seals the run.
# --------------------------------------------------------------------------- #


def test_analyze_start_unknown_session_is_404(make_capture_client):
    r = make_capture_client().post(f"/api/sessions/{uuid.uuid4().hex}/analyze/start")
    assert r.status_code == 404


def test_analyze_start_emits_event_and_enqueues_one_walk(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(
        client,
        sid,
        [
            _file("https://acme.io/a.js", "fetch('/api/v1/users');", sid),
            _file("https://acme.io/b.js", "b();", sid),
        ],
    )
    body = client.post(f"/api/sessions/{sid}/analyze/start").json()
    assert body["started"] is True and body["job"]
    run_id, tid = body["runId"], _tenant_id(client.capture_tenant_name)

    event = discover_queries.latest_assets_event(tid, run_id)
    assert event and event["status"] == "ok" and event["count"] == 2
    with tenant_session(tid) as s:
        n_jobs = s.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.run_id == run_id, Job.stage == "discovering")
        )
        assert n_jobs == 1


def test_analyze_start_is_idempotent(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(client, sid, [_file("https://acme.io/a.js", "a();", sid)])
    b1 = client.post(f"/api/sessions/{sid}/analyze/start").json()
    b2 = client.post(f"/api/sessions/{sid}/analyze/start").json()
    assert b1["started"] and b2["started"]
    tid = _tenant_id(client.capture_tenant_name)
    with tenant_session(tid) as s:
        n_jobs = s.scalar(select(func.count()).select_from(Job).where(Job.run_id == b1["runId"]))
        assert n_jobs == 1  # the second call did not enqueue a second walk


def test_analyze_start_seals_run_next_batch_opens_new_round(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    b1 = _save(client, sid, [_file("https://acme.io/a.js", "a();", sid)])
    client.post(f"/api/sessions/{sid}/analyze/start")  # seals run 1
    b2 = _save(client, sid, [_file("https://acme.io/b.js", "b();", sid)])
    assert b2["runId"] != b1["runId"]  # a fresh capture round


def test_analyze_start_with_no_captured_files_is_a_clean_noop(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    # one invalid file -> session + run exist but with zero assets.
    _save(client, sid, [{"url": "https://acme.io/x.js"}])
    r = client.post(f"/api/sessions/{sid}/analyze/start").json()
    assert r["started"] is False


# --------------------------------------------------------------------------- #
# End-to-end: upload -> analyze/start -> REAL worker -> findings, run DONE.
# --------------------------------------------------------------------------- #


def test_end_to_end_upload_analyze_to_findings(make_capture_client, redis, monkeypatch):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(
        client,
        sid,
        [
            _file("https://acme.io/a.js", "fetch('/api/v1/users');", sid),
            _file(
                "https://acme.io/b.js",
                "fetch('/api/v1/users'); fetch('/v2/orders',{method:'POST'});",
                sid,
            ),
        ],
    )
    started = client.post(f"/api/sessions/{sid}/analyze/start").json()
    assert started["started"]
    run_id, tid = started["runId"], _tenant_id(client.capture_tenant_name)

    # Prove the walk does ZERO network egress: the pre-fetched assets make discover
    # short-circuit and fetch skip, so neither fetch_url nor the egress guard is
    # ever reached — captured post-auth URLs must never be re-requested.
    calls = {"fetch_url": 0, "validate_target": 0}
    real_fetch, real_validate = fetch.fetch_url, egress.validate_target
    monkeypatch.setattr(
        fetch,
        "fetch_url",
        lambda *a, **k: (
            calls.__setitem__("fetch_url", calls["fetch_url"] + 1),
            real_fetch(*a, **k),
        )[1],
    )
    monkeypatch.setattr(
        egress,
        "validate_target",
        lambda *a, **k: (
            calls.__setitem__("validate_target", calls["validate_target"] + 1),
            real_validate(*a, **k),
        )[1],
    )

    # Drive the REAL worker. No katana/network: discover short-circuits on the
    # event, fetch skips every pre-fetched asset, analyze does the real extraction.
    flags = None
    for _ in range(80):
        worker.run_once(redis, "capture-test-worker", block_ms=50)
        flags = run_queries.get_run_flags(tid, run_id)
        if flags and sm.is_terminal(RunState(flags.state)):
            break
    assert flags is not None and flags.state == RunState.DONE.value
    assert calls == {"fetch_url": 0, "validate_target": 0}  # no egress at all

    view = findings_queries.list_findings(tid, run_id)
    endpoints = {f.value for f in view.findings if f.type == "endpoint"}
    assert "GET /api/v1/users" in endpoints
    assert "POST /v2/orders" in endpoints
    # the shared endpoint dedupes across both assets to one finding, two occurrences
    shared = next(f for f in view.findings if f.value == "GET /api/v1/users")
    assert len(shared.occurrences) == 2


# --------------------------------------------------------------------------- #
# Phase 2: analyze/progress adapter.
# --------------------------------------------------------------------------- #


def test_progress_unknown_session_is_404(make_capture_client):
    r = make_capture_client().get(f"/api/sessions/{uuid.uuid4().hex}/analyze/progress")
    assert r.status_code == 404


def test_progress_before_analyze_is_idle(make_capture_client):
    # Captured but not analyzed (QUEUED run, no Job): progress must read IDLE so the
    # popup keeps the Analyze button live — never a stuck "running".
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(client, sid, [_file("https://acme.io/a.js", "a();", sid)])
    job = client.get(f"/api/sessions/{sid}/analyze/progress").json()["job"]
    assert job["counts"] == {
        "queued": 0,
        "analyzing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "total": 0,
    }
    assert job["files"] == []


def test_progress_after_start_before_worker_is_running(make_capture_client):
    # analyze/start enqueued the run (it now has a Job) but the worker hasn't run:
    # pending assets read "queued" so the popup shows running (inFlight > 0).
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(
        client,
        sid,
        [_file("https://acme.io/a.js", "a();", sid), _file("https://acme.io/b.js", "b();", sid)],
    )
    client.post(f"/api/sessions/{sid}/analyze/start")
    job = client.get(f"/api/sessions/{sid}/analyze/progress").json()["job"]
    assert job["counts"]["total"] == 2 and job["counts"]["queued"] == 2
    assert {f["url"] for f in job["files"]} == {"https://acme.io/a.js", "https://acme.io/b.js"}
    assert all(f["status"] == "queued" for f in job["files"])


def test_progress_after_worker_is_completed(make_capture_client, redis):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(client, sid, [_file("https://acme.io/a.js", "fetch('/api/v1/users');", sid)])
    started = client.post(f"/api/sessions/{sid}/analyze/start").json()
    tid = _tenant_id(client.capture_tenant_name)
    for _ in range(80):
        worker.run_once(redis, "capture-progress-worker", block_ms=50)
        flags = run_queries.get_run_flags(tid, started["runId"])
        if flags and sm.is_terminal(RunState(flags.state)):
            break
    job = client.get(f"/api/sessions/{sid}/analyze/progress").json()["job"]
    assert job["counts"]["completed"] == 1 and job["counts"]["queued"] == 0
    assert job["files"] == [{"url": "https://acme.io/a.js", "status": "completed"}]


def test_progress_terminal_run_with_pending_asset_settles(make_capture_client, redis, monkeypatch):
    # Abnormal termination: analyze fails fatally -> run FAILED with the asset still
    # pending. Progress must SETTLE (pending -> failed) so the popup's inFlight hits
    # 0 and the Analyze button unblocks, instead of polling "running" forever.
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(client, sid, [_file("https://acme.io/a.js", "a();", sid)])
    started = client.post(f"/api/sessions/{sid}/analyze/start").json()
    tid = _tenant_id(client.capture_tenant_name)
    monkeypatch.setattr(
        analyze_mod,
        "analyze_run",
        lambda *a, **k: (_ for _ in ()).throw(retry.FatalError("analyze exploded")),
    )
    flags = None
    for _ in range(80):
        worker.run_once(redis, "capture-fail-worker", block_ms=50)
        flags = run_queries.get_run_flags(tid, started["runId"])
        if flags and sm.is_terminal(RunState(flags.state)):
            break
    assert flags is not None and flags.state == RunState.FAILED.value
    job = client.get(f"/api/sessions/{sid}/analyze/progress").json()["job"]
    assert job["counts"]["queued"] == 0 and job["counts"]["analyzing"] == 0  # settled
    assert job["counts"]["failed"] == 1


# --------------------------------------------------------------------------- #
# Phase 2: projects <-> engagements adapter.
# --------------------------------------------------------------------------- #


def test_projects_get_is_a_bare_array(make_capture_client):
    r = make_capture_client().get("/api/projects")
    assert r.status_code == 200 and isinstance(r.json(), list)  # NOT an object envelope


def test_create_then_list_project(make_capture_client):
    client = make_capture_client()
    created = client.post(
        "/api/projects",
        json={
            "name": "Acme engagement",
            "defaults": {"scope": {"rootDomains": ["acme.io", "api.acme.io"]}},
        },
    ).json()
    assert created["id"] and created["name"] == "Acme engagement"
    assert created["defaults"]["scope"]["rootDomains"] == ["acme.io", "api.acme.io"]
    assert created["defaults"]["scope"]["includeSubdomains"] is True
    listed = client.get("/api/projects").json()
    assert isinstance(listed, list)
    assert any(p["id"] == created["id"] and p["name"] == "Acme engagement" for p in listed)


def test_create_project_blank_name_is_400(make_capture_client):
    r = make_capture_client().post("/api/projects", json={"name": "   ", "defaults": {}})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Phase 2: defensive project binding on save-files.
# --------------------------------------------------------------------------- #


def test_save_files_binds_a_valid_project(make_capture_client):
    client = make_capture_client()
    pid = client.post(
        "/api/projects", json={"name": "P", "defaults": {"scope": {"rootDomains": ["acme.io"]}}}
    ).json()["id"]
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    body = client.post(
        "/api/save-files",
        json={
            "metadata": {"sessionId": sid, "projectId": pid},
            "files": [_file("https://acme.io/a.js", "a();", sid)],
        },
    ).json()
    tid = _tenant_id(client.capture_tenant_name)  # tenant exists once a request created it
    assert sessions_service.get_session(tid, body["sessionId"]).engagement_id == pid


def test_save_files_ignores_garbage_or_foreign_project(make_capture_client):
    # §4 defect-A regression guard: an invalid projectId must NOT raise (a 4xx would
    # drop un-recapturable JS; a 5xx would retry-loop). The session is created UNBOUND
    # and the batch still succeeds.
    client = make_capture_client()
    for bad in ("not-a-uuid", str(uuid.uuid4())):  # malformed, then valid-but-unknown
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        r = client.post(
            "/api/save-files",
            json={
                "metadata": {"sessionId": sid, "projectId": bad},
                "files": [_file("https://acme.io/a.js", "a();", sid)],
            },
        )
        assert r.status_code == 200
        tid = _tenant_id(client.capture_tenant_name)  # tenant exists after the request
        assert sessions_service.get_session(tid, r.json()["sessionId"]).engagement_id is None


# --------------------------------------------------------------------------- #
# Phase 3: per-asset source map ingest -> source_map blob + run_asset.source_map_ref.
# --------------------------------------------------------------------------- #

_MAP = {"version": 3, "sources": ["app/src/a.js"], "mappings": "AAAA"}


def _asset(tid: str, run_id: str, url: str):
    return next(r for r in run_assets.list_for_run(tid, run_id) if r.url == url)


def test_save_files_stores_source_map_and_links_asset(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    f = {**_file("https://acme.io/a.js", "a();", sid), "sourceMapContent": _MAP}
    body = _save(client, sid, [f])
    tid = _tenant_id(client.capture_tenant_name)
    row = _asset(tid, body["runId"], "https://acme.io/a.js")
    assert row.source_map_ref and "/source_map/" in row.source_map_ref
    assert json.loads(storage.get_blob(row.source_map_ref)) == _MAP  # the serialized map


def test_save_files_without_map_leaves_ref_none(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    body = _save(client, sid, [_file("https://acme.io/a.js", "a();", sid)])
    tid = _tenant_id(client.capture_tenant_name)
    assert _asset(tid, body["runId"], "https://acme.io/a.js").source_map_ref is None


def test_save_files_source_map_retry_is_first_wins(make_capture_client):
    # A later same-url batch with a DIFFERENT map must not clobber the first (mirrors
    # the input_ref first-wins rule) — the original recovery stays stable.
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    b1 = _save(
        client, sid, [{**_file("https://acme.io/a.js", "a();", sid), "sourceMapContent": _MAP}]
    )
    tid = _tenant_id(client.capture_tenant_name)
    ref1 = _asset(tid, b1["runId"], "https://acme.io/a.js").source_map_ref
    other = {"version": 3, "sources": ["other.js"], "mappings": "BBBB"}
    _save(client, sid, [{**_file("https://acme.io/a.js", "a();", sid), "sourceMapContent": other}])
    assert _asset(tid, b1["runId"], "https://acme.io/a.js").source_map_ref == ref1  # unchanged


def test_save_files_oversized_map_skipped_file_still_stored(make_capture_client):
    # A map over the byte cap is dropped; the JS is still stored + analyzable.
    client = make_capture_client(RECON_MAX_UPLOAD_BYTES=64)
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    big_map = {"version": 3, "sources": ["x"], "mappings": "A" * 200}
    body = _save(
        client, sid, [{**_file("https://acme.io/a.js", "a();", sid), "sourceMapContent": big_map}]
    )
    assert body["stored"] == 1 and body["failed"] == 0
    tid = _tenant_id(client.capture_tenant_name)
    row = _asset(tid, body["runId"], "https://acme.io/a.js")
    assert row.input_ref and row.source_map_ref is None


def test_save_files_non_object_map_skipped(make_capture_client):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    body = _save(
        client,
        sid,
        [{**_file("https://acme.io/a.js", "a();", sid), "sourceMapContent": "not-an-object"}],
    )
    assert body["stored"] == 1
    tid = _tenant_id(client.capture_tenant_name)
    assert _asset(tid, body["runId"], "https://acme.io/a.js").source_map_ref is None


# --------------------------------------------------------------------------- #
# DEBT D1: get-or-create is race-safe. Two concurrent writers for the same
# ext sessionId must resolve to ONE session and ONE open run — never silently
# duplicate rows that analyze/start would split, orphaning captured post-auth JS.
# Each test forces the interleave with a threading.Barrier at the lookup seam, so
# both writers finish their existence-check (miss) before either inserts; the
# unique key then rejects the loser, which self-heals to the winner. Without the
# unique index both inserts would commit -> two rows (verified red).
# --------------------------------------------------------------------------- #


def _run_two_writers(target):
    """Run ``target`` (a no-arg callable returning an id) in two threads and return
    both results. A thread's exception is re-raised so the test fails, not hangs."""
    results: dict[int, object] = {}  # id (D1 races) or response dict (D14 race)
    errors: list[BaseException] = []

    def worker(idx: int) -> None:
        try:
            results[idx] = target()
        except BaseException as exc:  # noqa: BLE001 - surfaced via the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    if errors:
        raise errors[0]
    assert len(results) == 2, "a writer thread did not finish within the timeout"
    return results[0], results[1]


def test_concurrent_batches_resolve_to_one_session(make_capture_client, monkeypatch):
    client = make_capture_client()
    tid = capture_router._get_or_create_tenant(client.capture_tenant_name)
    sid = f"sess-{uuid.uuid4().hex[:8]}"

    barrier = threading.Barrier(2, timeout=30)
    real_find = capture_router._find_session_by_external_id

    def rendezvous_find(tenant_id: str, ext_session_id: str):
        found = real_find(tenant_id, ext_session_id)
        if found is None:  # both threads meet here after their miss, before inserting
            barrier.wait()
        return found

    monkeypatch.setattr(capture_router, "_find_session_by_external_id", rendezvous_find)

    a, b = _run_two_writers(lambda: capture_router._get_or_create_session(tid, sid))

    assert a == b  # both callers got the same session id
    with tenant_session(tid) as s:
        n = s.scalar(
            select(func.count())
            .select_from(EngagementSession)
            .where(EngagementSession.external_id == sid)
        )
    assert n == 1  # exactly one session, no orphan


def test_concurrent_first_batches_resolve_to_one_run(make_capture_client, redis, monkeypatch):
    client = make_capture_client()
    tid = capture_router._get_or_create_tenant(client.capture_tenant_name)
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    session_id = capture_router._get_or_create_session(tid, sid)  # session exists, no run yet

    barrier = threading.Barrier(2, timeout=30)
    real_find = capture_router._find_open_capture_run

    def rendezvous_find(tenant_id: str, ext_session_id: str):
        found = real_find(tenant_id, ext_session_id)
        if found is None:
            barrier.wait()
        return found

    monkeypatch.setattr(capture_router, "_find_open_capture_run", rendezvous_find)

    a, b = _run_two_writers(
        lambda: capture_router._accumulating_run_id(tid, session_id, sid, redis)
    )

    assert a == b  # both batches accumulate into the same run
    with tenant_session(tid) as s:
        n = s.scalar(select(func.count()).select_from(Run).where(Run.capture_external_id == sid))
    assert n == 1  # exactly one open accumulator run


# --------------------------------------------------------------------------- #
# DEBT D14: concurrent analyze/start enqueues exactly ONE walk. Two simultaneous
# calls for one session both clear the _run_has_job fast-path gate (barrier-synced
# at that seam), then race the guarded seal; the loser's guarded UPDATE matches 0
# rows and returns idempotent instead of enqueuing a second DISCOVERING walk. Red
# without the seal-CAS (both inserts commit — no unique key on (run_id, stage)),
# green with it.
# --------------------------------------------------------------------------- #


def test_concurrent_analyze_start_enqueues_one_walk(make_capture_client, monkeypatch):
    client = make_capture_client()
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    _save(client, sid, [_file("https://acme.io/a.js", "fetch('/api/v1/users');", sid)])
    tid = _tenant_id(client.capture_tenant_name)

    barrier = threading.Barrier(2, timeout=30)
    real_has_job = capture_router._run_has_job

    def rendezvous_has_job(tenant_id: str, run_id: str) -> bool:
        found = real_has_job(tenant_id, run_id)
        if not found:  # both callers meet past the gate, before either seals
            barrier.wait()
        return found

    monkeypatch.setattr(capture_router, "_run_has_job", rendezvous_has_job)

    # Call the endpoint fn directly in two threads (not via TestClient, whose portal
    # would serialize the requests and deadlock the barrier) — mirrors the D1 tests.
    a, b = _run_two_writers(lambda: capture_router.analyze_start(sid))

    assert a["started"] is True and b["started"] is True  # both callers see success
    assert sum("job" in r for r in (a, b)) == 1  # exactly one winner returned a job
    run_id = a.get("runId") or b.get("runId")
    with tenant_session(tid) as s:
        n_jobs = s.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.run_id == run_id, Job.stage == "discovering")
        )
    assert n_jobs == 1  # exactly one walk enqueued, not two
