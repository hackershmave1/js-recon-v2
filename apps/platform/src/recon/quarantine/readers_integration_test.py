"""No reader of run assets returns quarantined content (ADR-0010, S1 acceptance): one test per
reader, each against a run that holds both a normal asset and a quarantined script, and each
asserting the quarantined script's url, host and bytes are nowhere in what the reader returns."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass

import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import hosts, queries, techdetect_pass
from recon.probe import source_search, sources
from recon.probe.reconstruct import reconstruct_run
from recon.quarantine import service
from recon.runs import assets as run_assets
from recon.runs import coordinator
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

Q_HOST = "cdn.quarantined-host.example"
Q_URL = f"https://{Q_HOST}/hidden.js"
MARKER = "QUARANTINE_MARKER_9f3a"
Q_BYTES = f"fetch('/api/{MARKER}'); // {MARKER}".encode()
NORMAL = b"fetch('/api/visible');"


@pytest.fixture()
def run():
    tenant = sessions_service.create_tenant(f"q-readers-{uuid.uuid4().hex[:8]}")
    session_view = sessions_service.create_session(
        tenant, name="r", scope_hosts=["acme.io"], authorized_by="t"
    )
    with tenant_session(tenant) as session:
        row = models.Run(tenant_id=tenant, session_id=session_view.id, state="done")
        session.add(row)
        session.flush()
        run_id = str(row.id)
        key = storage.put_blob(tenant, run_id, "input", NORMAL)
        session.add(
            models.RunAsset(
                tenant_id=tenant,
                run_id=run_id,
                url="https://acme.io/app.js",
                input_ref=key,
                fetch_status="ok",
                analyze_status="ok",
            )
        )
    [stored] = service.admit(
        tenant_id=tenant,
        session_id=session_view.id,
        run_id=run_id,
        host=Q_HOST,
        files=[service.FileIn(Q_URL, Q_BYTES)],
    )
    assert stored.outcome == service.STORED
    return tenant, session_view.id, run_id


def _text(value) -> str:
    """Everything a reader returned, as one string (dataclasses, lists and dicts alike)."""

    def plain(v):
        if is_dataclass(v) and not isinstance(v, type):
            return plain(asdict(v))
        if isinstance(v, dict):
            return {str(k): plain(x) for k, x in v.items()}
        if isinstance(v, list | tuple | set):
            return [plain(x) for x in v]
        return v

    return json.dumps(plain(value), default=str)


def _assert_absent(value) -> None:
    text = _text(value)
    for needle in (Q_URL, Q_HOST, MARKER):
        assert needle not in text, f"quarantined {needle!r} leaked into a reader"


def test_findings_and_coverage(run):
    tenant, _, run_id = run
    view = queries.list_findings(tenant, run_id, include_hidden=True)
    _assert_absent(view)  # the view carries the coverage counters too


def test_sources_listing(run):
    tenant, _, run_id = run
    listing = sources.list_sources(tenant, run_id)
    _assert_absent(listing)
    assert [s.path for s in listing] == ["https://acme.io/app.js"]


def test_sources_asset_keys(run):
    tenant, _, run_id = run
    _assert_absent(sources.asset_keys(tenant, run_id))


def test_sources_content_cannot_be_read_by_url(run):
    tenant, _, run_id = run
    assert sources.get_source_content(tenant, run_id, Q_URL) is None


def test_hosts_inventory(run):
    tenant, _, run_id = run
    _assert_absent(hosts.list_hosts(tenant, run_id, include_noise=True))


def test_run_assets_listing(run):
    tenant, _, run_id = run
    rows = run_assets.list_for_run(tenant, run_id)
    _assert_absent(rows)
    assert [r.url for r in rows] == ["https://acme.io/app.js"]


def test_tech_detection_inputs(run):
    tenant, _, run_id = run
    texts = techdetect_pass._js_texts_by_host(tenant, run_id, hosts={"acme.io", Q_HOST})
    _assert_absent(texts)  # the pass reads its script text through the run-asset listing


def test_search_does_not_find_quarantined_text(run):
    tenant, _, run_id = run
    outcome = source_search.search(tenant, run_id, MARKER)
    assert outcome is not None and outcome.total == 0
    _assert_absent(outcome)


def test_export_reconstruction(run):
    tenant, _, run_id = run
    _assert_absent(reconstruct_run(tenant, run_id))


def test_run_completeness_ignores_quarantine(run):
    tenant, _, run_id = run
    _state, completeness, _due = coordinator._finalize_state(tenant, run_id)
    assert completeness == {"fetch_ok": True, "analyze_ok": True}


def test_session_summary_counts_no_quarantined_file(run):
    tenant, session_id, _ = run
    summary = queries.get_session_findings_summary(tenant, session_id)
    _assert_absent(summary)
