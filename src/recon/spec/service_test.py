"""Colocated integration tests for `recon.spec.service` (design §6.3).

Mirrors `probe/triage_test.py`'s seeding style (a bare `models.Run` inserted
directly under a fresh tenant/session, DB-only, no worker/redis) rather than
`findings/queries_test.py`'s worker-driven style, since attach/classify never
touches the queue -- it is a pure DB + blob-store service, exactly like triage.
Each test creates its own tenant (RLS-isolated) so tests never interfere.
"""

from __future__ import annotations

import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service
from recon.spec import service
from recon.spec.ingest import SpecError

pytestmark = pytest.mark.integration

# A minimal, schema-valid OpenAPI 3.0 doc documenting exactly one operation.
# Flow-mapping style mirrors `spec/ingest_test.py`'s OPENAPI3 fixture.
OPENAPI_WITH_LOCATION = b"""openapi: 3.0.0
info: {title: t, version: '1'}
paths: {/location/address/search: {get: {responses: {'200': {description: ok}}}}}
"""

# A second, DIFFERENT spec (documents a different op entirely) so re-attaching
# it flips which of the two seeded findings is documented vs. shadow -- proof
# the re-tag is a real re-classification, not a coincidental no-op.
OPENAPI_WITH_ADMIN_WIPE = b"""openapi: 3.0.0
info: {title: t, version: '1'}
paths: {/admin/wipe: {post: {responses: {'200': {description: ok}}}}}
"""


def _run(tenant: str, session_id: str) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def _seed_endpoint(tenant: str, run_id: str, value: str) -> str:
    """Record one ENDPOINT finding on `run_id` and return its `finding_hash`."""
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value=value, path="input.js",
            occurrence=store.Occurrence(host="acme.io", raw_url="/x"),
            attributes={"method": value.split(" ", 1)[0]}, first_stage="analyzing",
        )
        return result.finding_hash


def test_attach_classifies_endpoint_findings():
    tenant = sessions_service.create_tenant("spec-1")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    documented_hash = _seed_endpoint(tenant, run_id, "GET /location/address/search")
    shadow_hash = _seed_endpoint(tenant, run_id, "POST /admin/wipe")

    summary = service.attach_and_classify(tenant, run_id, OPENAPI_WITH_LOCATION)

    assert summary is not None
    assert summary.documented == 1
    assert summary.shadow == 1
    assert summary.unresolved == 0

    with tenant_session(tenant) as session:
        rows = {
            row.finding_hash: row
            for row in session.query(models.FindingSpecStatus)
            .filter_by(session_id=str(session_view.id))
            .all()
        }
        assert rows[documented_hash].status == "documented"
        assert rows[documented_hash].matched_operation == "GET /location/address/search"
        assert rows[shadow_hash].status == "shadow"
        assert rows[shadow_hash].matched_operation is None

        # REQ-S3: a durable, value-free audit event with the per-bucket counts.
        events = (
            session.query(models.RunEvent)
            .filter_by(run_id=run_id, type="spec.classified")
            .all()
        )
        assert len(events) == 1
        assert events[0].payload["documented"] == 1
        assert events[0].payload["shadow"] == 1


def test_reclassify_noop_without_session_spec():
    tenant = sessions_service.create_tenant("spec-2")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    _seed_endpoint(tenant, run_id, "GET /location/address/search")

    assert service.reclassify_run(tenant, run_id) is None


def test_reclassify_run_classifies_from_stored_spec():
    tenant = sessions_service.create_tenant("spec-7")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_a = _run(tenant, session_view.id)
    _seed_endpoint(tenant, run_a, "GET /location/address/search")
    service.attach_and_classify(tenant, run_a, OPENAPI_WITH_LOCATION)

    # REQ-D5: a LATER run in the SAME session surfaces a new endpoint that
    # did not exist when the spec was attached. The auto-reclassify hook
    # (Task 11) calls reclassify_run against the ALREADY-stored spec -- no
    # new upload -- so this exercises reclassify_run's actual success path
    # (get_blob + ingest_spec + _classify_session), not just its no-op guard.
    run_b = _run(tenant, session_view.id)
    new_hash = _seed_endpoint(tenant, run_b, "POST /admin/wipe")

    summary = service.reclassify_run(tenant, run_b)

    assert summary is not None
    assert summary.shadow == 1
    assert summary.documented == 0

    with tenant_session(tenant) as session:
        row = (
            session.query(models.FindingSpecStatus)
            .filter_by(session_id=str(session_view.id), finding_hash=new_hash)
            .one()
        )
        assert row.status == "shadow"


def test_reattach_retags():
    tenant = sessions_service.create_tenant("spec-3")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    location_hash = _seed_endpoint(tenant, run_id, "GET /location/address/search")
    wipe_hash = _seed_endpoint(tenant, run_id, "POST /admin/wipe")

    summary_a = service.attach_and_classify(tenant, run_id, OPENAPI_WITH_LOCATION)
    assert summary_a.documented == 1 and summary_a.shadow == 1

    summary_b = service.attach_and_classify(tenant, run_id, OPENAPI_WITH_ADMIN_WIPE)
    # Statuses FLIP: spec B documents /admin/wipe, not /location/address/search.
    assert summary_b.documented == 1 and summary_b.shadow == 1

    with tenant_session(tenant) as session:
        rows = {
            row.finding_hash: row
            for row in session.query(models.FindingSpecStatus)
            .filter_by(session_id=str(session_view.id))
            .all()
        }
        # Exactly 2 rows after TWO attaches -- re-attach must UPDATE in place,
        # never insert a second row for the same (session_id, finding_hash).
        assert len(rows) == 2
        assert rows[location_hash].status == "shadow"
        assert rows[wipe_hash].status == "documented"

        session_spec = (
            session.query(models.SessionSpec)
            .filter_by(session_id=str(session_view.id))
            .one()
        )
        expected_ref_b = _object_key_of(tenant, run_id, OPENAPI_WITH_ADMIN_WIPE)
        assert session_spec.spec_ref == expected_ref_b
        assert rows[location_hash].spec_ref == expected_ref_b
        assert rows[wipe_hash].spec_ref == expected_ref_b


def test_tenant_isolation_on_finding_spec_status():
    tenant_a = sessions_service.create_tenant("spec-4a")
    session_view_a = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id_a = _run(tenant_a, session_view_a.id)
    _seed_endpoint(tenant_a, run_id_a, "GET /location/address/search")
    service.attach_and_classify(tenant_a, run_id_a, OPENAPI_WITH_LOCATION)

    tenant_b = sessions_service.create_tenant("spec-4b")
    with tenant_session(tenant_b) as session:
        # Same (leaked) session_id, a DIFFERENT tenant's RLS scope -- RLS must
        # filter these rows out entirely, not merely deny by-id lookup.
        rows = (
            session.query(models.FindingSpecStatus)
            .filter_by(session_id=str(session_view_a.id))
            .all()
        )
        assert rows == []


def test_attach_unknown_run_returns_none():
    tenant = sessions_service.create_tenant("spec-5")
    missing_run = "00000000-0000-0000-0000-000000000000"
    assert service.attach_and_classify(tenant, missing_run, OPENAPI_WITH_LOCATION) is None


def test_attach_invalid_spec_raises_specerror():
    # SpecError must PROPAGATE (never be swallowed) -- the router (Task 9)
    # depends on this to map an untrusted/malformed spec to HTTP 422.
    tenant = sessions_service.create_tenant("spec-6")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    run_id = _run(tenant, session_view.id)
    with pytest.raises(SpecError):
        service.attach_and_classify(tenant, run_id, b"not a spec")


def _object_key_of(tenant: str, run_id: str, content: bytes) -> str:
    """The content-addressed key `storage.put_blob` would return for `content`
    -- a pure recomputation (no second write) used only to assert the stored
    `spec_ref` matches the SECOND upload, not the first."""
    return storage.object_key(tenant, run_id, "spec", content)
