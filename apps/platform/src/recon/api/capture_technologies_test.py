"""DEBT D77: an extension capture run gets tech detection.

Drives the real ingest contract (capture/start -> save-files with each file's response
``headers`` -> capture/end) and then the analyze stage, and asserts ``run_technology``
rows come from what the extension uploaded: a response header (server) and a library
banner in the script's own bytes."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from recon import storage
from recon.api.app import create_app
from recon.config import get_settings
from recon.db.base import admin_session, tenant_session
from recon.db.models import RunEvent, RunTechnology, Tenant
from recon.findings import analyze

pytestmark = pytest.mark.integration

# A real library banner (Ember's own header comment) — matched in the script's BYTES by the
# dataset's ``scripts`` surface, version included.
_EMBER = (
    "/*!\n * @overview  Ember - JavaScript Application Framework\n"
    " * @license   Licensed under MIT license\n * @version   3.28.12\n */\n!function(){}();"
)


@pytest.fixture()
def client(monkeypatch, redis):
    name = f"capture-tech-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("RECON_ENABLE_CAPTURE_INGEST", "true")
    monkeypatch.setenv("RECON_CAPTURE_TENANT_NAME", name)
    get_settings.cache_clear()
    c = TestClient(create_app())
    c.tenant_name = name  # type: ignore[attr-defined]
    yield c
    get_settings.cache_clear()


def _tenant(c) -> str:
    with admin_session() as s:
        return str(s.scalar(select(Tenant.id).where(Tenant.name == c.tenant_name)))


def _file(url: str, content: str, sid: str, headers: dict | None) -> dict:
    f = {
        "url": url,
        "content": content,
        "sessionId": sid,
        "contentHash": hashlib.sha256(content.encode()).hexdigest()[:16],
        "contentLength": len(content),
    }
    if headers is not None:
        f["headers"] = headers
    return f


def _capture(c, sid: str, *batches: list[dict]) -> str:
    r = c.post(f"/api/sessions/{sid}/capture/start", json={"rootDomains": ["acme.io"]})
    assert r.status_code == 200, r.text
    run_id = r.json()["runId"]
    for files in batches:
        saved = c.post(
            "/api/save-files",
            json={"metadata": {"sessionId": sid, "runId": run_id}, "files": files},
        )
        assert saved.status_code == 200 and saved.json()["stored"] == len(files), saved.text
    end = c.post(f"/api/sessions/{sid}/capture/end", json={"runId": run_id})
    assert end.status_code == 200, end.text
    return run_id


def test_a_capture_run_gets_run_technology_from_uploaded_headers_and_scripts(client, redis):
    sid = str(uuid.uuid4())
    run_id = _capture(
        client,
        sid,
        [
            _file(
                "https://app.acme.io/static/jquery.min.js",
                _EMBER,
                sid,
                {"server": "nginx/1.25.3", "content-type": "application/javascript"},
            )
        ],
        # A second batch on another host: the pass must merge every batch's signal,
        # not read only the latest.
        [_file("https://cdn.acme.io/app.js", "console.log(1)", sid, {"server": "Apache"})],
    )
    tenant = _tenant(client)

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    with tenant_session(tenant) as s:
        rows = {(r.host, r.name): r for r in s.scalars(select(RunTechnology)).all()}
    assert rows[("app.acme.io", "Nginx")].version == "1.25.3"
    assert rows[("app.acme.io", "Ember.js")].version == "3.28.12"  # from the banner
    assert ("app.acme.io", "jQuery") in rows  # from the script URL (scriptSrc)
    assert ("cdn.acme.io", "Apache HTTP Server") in rows


def test_the_stored_signal_keeps_only_allowlisted_headers(client, redis):
    sid = str(uuid.uuid4())
    run_id = _capture(
        client,
        sid,
        [
            _file(
                "https://app.acme.io/a.js",
                "a()",
                sid,
                {"server": "nginx", "set-cookie": "sid=VALUE; path=/", "x-request-id": "r-1"},
            )
        ],
    )
    tenant = _tenant(client)
    with tenant_session(tenant) as s:
        refs = s.scalars(
            select(RunEvent.payload).where(
                RunEvent.run_id == run_id, RunEvent.type == "fingerprint.signal"
            )
        ).all()
    assert len(refs) == 1
    signal = json.loads(storage.get_blob(refs[0]["signal_ref"]))
    assert signal["app.acme.io"]["headers"] == {"server": "nginx"}
    assert signal["app.acme.io"]["cookies"] == ["sid"]
    assert "VALUE" not in json.dumps(signal) and "r-1" not in json.dumps(signal)


def test_a_capture_with_no_headers_still_detects_from_script_bytes(client, redis):
    sid = str(uuid.uuid4())
    run_id = _capture(client, sid, [_file("https://app.acme.io/app.js", _EMBER, sid, None)])
    tenant = _tenant(client)

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    with tenant_session(tenant) as s:
        names = set(s.scalars(select(RunTechnology.name)).all())
    assert "Ember.js" in names


def _signal_events(tenant: str, run_id: str) -> list[dict]:
    with tenant_session(tenant) as s:
        return list(
            s.scalars(
                select(RunEvent.payload).where(
                    RunEvent.run_id == run_id, RunEvent.type == "fingerprint.signal"
                )
            ).all()
        )


def test_an_off_scope_third_party_gets_no_signal_and_no_tech_row(client, redis):
    # The capture declared acme.io; the page also loaded a tag manager. T1 parity with the
    # server-side capture: its headers and URL are never persisted, and it gets no row.
    sid = str(uuid.uuid4())
    run_id = _capture(
        client,
        sid,
        [
            _file("https://app.acme.io/a.js", "a()", sid, {"server": "nginx"}),
            _file("https://www.googletagmanager.com/gtm.js", "g()", sid, {"server": "Apache"}),
        ],
    )
    tenant = _tenant(client)
    [payload] = _signal_events(tenant, run_id)
    assert set(json.loads(storage.get_blob(payload["signal_ref"]))) == {"app.acme.io"}

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    with tenant_session(tenant) as s:
        hosts = set(s.scalars(select(RunTechnology.host)).all())
    assert hosts == {"app.acme.io"}


def test_a_batch_refused_as_ended_records_no_signal(client, monkeypatch):
    # The signal event rides the batch's row transaction: a batch whose capture closed while it
    # uploaded rolls back with no signal left behind.
    from recon.runs import capture_lifecycle

    sid = str(uuid.uuid4())
    r = client.post(f"/api/sessions/{sid}/capture/start", json={"rootDomains": ["acme.io"]})
    run_id = r.json()["runId"]
    monkeypatch.setattr(capture_lifecycle, "accept_batch", lambda *a, **k: False)

    saved = client.post(
        "/api/save-files",
        json={
            "metadata": {"sessionId": sid, "runId": run_id},
            "files": [_file("https://app.acme.io/a.js", "a()", sid, {"server": "nginx"})],
        },
    )

    assert saved.status_code == 409
    assert _signal_events(_tenant(client), run_id) == []


def test_a_signal_store_failure_never_fails_the_batch(client, monkeypatch):
    from recon.capture import ingest_signal

    def boom(*args, **kwargs):
        raise RuntimeError("signal builder exploded")

    monkeypatch.setattr(ingest_signal, "build_batch_signal", boom)
    sid = str(uuid.uuid4())

    run_id = _capture(client, sid, [_file("https://app.acme.io/a.js", "a()", sid, {"server": "x"})])

    assert _signal_events(_tenant(client), run_id) == []
