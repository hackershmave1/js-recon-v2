"""Integration coverage for the correlate stage against live Postgres + MinIO.

Exercises the real query wiring the host-lane fakes stand in for: the
``discover.assets`` ``requests_ref`` read, the blob load, ``list_findings``, and the
idempotent ``record_finding`` capture-occurrence write.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from recon import storage
from recon.correlate import stage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.events.log import record_event
from recon.findings import store
from recon.findings.normalize import finding_hash

pytestmark = pytest.mark.integration


def _seed_capture_run(tenant, session_id, *, value, path, observed):
    """A run with one host-less endpoint finding and a discover.assets event carrying a
    capture-requests blob — exactly what the capture stage persists for a capture run."""
    requests_ref = storage.put_blob(
        tenant, "seed", "capture-requests", json.dumps(observed).encode("utf-8")
    )
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="correlating")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value=value,
            path=path,
            occurrence=store.Occurrence(host=None, raw_url=None, engine="vespasian"),
            attributes={"method": value.split(" ", 1)[0], "kind": "fetch"},
            first_stage="analyzing",
        )
        record_event(
            session,
            tenant_id=tenant,
            run_id=run_id,
            event_type="discover.assets",
            payload={"count": 0, "assets_ref": None, "requests_ref": requests_ref, "status": "ok"},
        )
    return run_id


def _capture_occurrences(tenant, value, path):
    finding_hash_value = finding_hash("endpoint", value)
    with tenant_session(tenant) as session:
        finding = session.query(models.Finding).filter_by(finding_hash=finding_hash_value).one()
        return [(o.host, o.raw_url) for o in finding.occurrences if o.engine == "capture"]


def test_correlate_writes_capture_occurrence_from_observed_request(authorized_session):
    tenant, session_id = authorized_session
    value = "GET /${baseDomainName}/get-job-types"
    run_id = _seed_capture_run(
        tenant,
        session_id,
        value=value,
        path="input.js",
        observed=[{"method": "GET", "url": "https://api.acme.io/get-job-types"}],
    )
    with patch("recon.correlate.stage.publish"):
        stage.correlate_run(MagicMock(), tenant_id=tenant, run_id=run_id, job_id="j")

    assert _capture_occurrences(tenant, value, "input.js") == [
        ("api.acme.io", "https://api.acme.io/get-job-types")
    ]


def test_correlate_rerun_is_idempotent(authorized_session):
    tenant, session_id = authorized_session
    value = "POST /getJobId"
    run_id = _seed_capture_run(
        tenant,
        session_id,
        value=value,
        path="input.js",
        observed=[{"method": "POST", "url": "https://api.acme.io/getJobId"}],
    )
    with patch("recon.correlate.stage.publish"):
        stage.correlate_run(MagicMock(), tenant_id=tenant, run_id=run_id, job_id="j")
        stage.correlate_run(MagicMock(), tenant_id=tenant, run_id=run_id, job_id="j")

    # At-least-once redelivery must not double-write the occurrence.
    assert _capture_occurrences(tenant, value, "input.js") == [
        ("api.acme.io", "https://api.acme.io/getJobId")
    ]
