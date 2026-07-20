"""Integration tests for the findings outbox store against live Postgres.

Covers the REQ-A3 exactly-once write, REQ-C2 honesty (over-merges keep every
distinct occurrence), REQ-D3 per-run identity, REQ-D5 cross-run recurrence, and
REQ-S1 tenant isolation via RLS. Requires the compose stack + `migrated` fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import normalize
from recon.findings.store import Occurrence, record_finding
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _make_run(tenant: str, session_id: str) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id)
        session.add(run)
        session.flush()
        return str(run.id)


def _count(model, **eq) -> int:
    def run(session) -> int:
        stmt = select(func.count()).select_from(model)
        for col, val in eq.items():
            stmt = stmt.where(getattr(model, col) == val)
        return session.execute(stmt).scalar()

    return run


def test_record_finding_is_idempotent_on_retry(authorized_session):
    tenant, session_id = authorized_session
    run_id = _make_run(tenant, session_id)
    endpoint = normalize.normalize_endpoint("GET", "https://api.acme.io/users/42")
    occ = Occurrence(host=endpoint.host, raw_url="/users/42", source_path="app/api.js",
                     offset_start=10, offset_end=20)

    with tenant_session(tenant) as s:
        first = record_finding(s, tenant_id=tenant, run_id=run_id,
                               finding_type=FindingType.ENDPOINT, value=endpoint.value,
                               path="app/api.js", occurrence=occ, first_stage="analyzing")
    with tenant_session(tenant) as s:  # a stage retry re-emits the identical finding
        second = record_finding(s, tenant_id=tenant, run_id=run_id,
                                finding_type=FindingType.ENDPOINT, value=endpoint.value,
                                path="app/api.js", occurrence=occ, first_stage="analyzing")

    assert first.finding_created and first.occurrence_created
    assert not second.finding_created and not second.occurrence_created
    assert first.finding_hash == second.finding_hash

    with tenant_session(tenant) as s:
        assert _count(models.Finding, run_id=run_id)(s) == 1
        assert _count(models.FindingOccurrence, finding_id=first.finding_id)(s) == 1


def test_over_merge_keeps_distinct_occurrences(authorized_session):
    # Two different ids normalize to the SAME endpoint identity; C2 honesty
    # requires both raw sightings to survive as occurrences, not vanish.
    tenant, session_id = authorized_session
    run_id = _make_run(tenant, session_id)
    e1 = normalize.normalize_endpoint("GET", "https://api.acme.io/users/1")
    e2 = normalize.normalize_endpoint("GET", "https://api.acme.io/users/2")
    assert e1.value == e2.value

    with tenant_session(tenant) as s:
        r1 = record_finding(s, tenant_id=tenant, run_id=run_id,
                            finding_type=FindingType.ENDPOINT, value=e1.value, path="app/api.js",
                            occurrence=Occurrence(host=e1.host, raw_url="/users/1",
                                                  source_path="app/api.js", offset_start=1, offset_end=2))
        r2 = record_finding(s, tenant_id=tenant, run_id=run_id,
                            finding_type=FindingType.ENDPOINT, value=e2.value, path="app/api.js",
                            occurrence=Occurrence(host=e2.host, raw_url="/users/2",
                                                  source_path="app/api.js", offset_start=3, offset_end=4))

    assert r1.finding_hash == r2.finding_hash
    assert r1.finding_created and not r2.finding_created  # one identity
    assert r1.occurrence_created and r2.occurrence_created  # two sightings kept
    with tenant_session(tenant) as s:
        assert _count(models.Finding, run_id=run_id)(s) == 1
        assert _count(models.FindingOccurrence, finding_id=r1.finding_id)(s) == 2


def test_same_identity_in_two_runs_is_two_findings(authorized_session):
    # REQ-D5: a finding recurs with the same hash across runs (per-run uniqueness).
    tenant, session_id = authorized_session
    run_a = _make_run(tenant, session_id)
    run_b = _make_run(tenant, session_id)
    endpoint = normalize.normalize_endpoint("GET", "/health")
    occ = Occurrence(raw_url="/health", source_path="app/api.js")

    with tenant_session(tenant) as s:
        ra = record_finding(s, tenant_id=tenant, run_id=run_a, finding_type=FindingType.ENDPOINT,
                            value=endpoint.value, path="app/api.js", occurrence=occ)
        rb = record_finding(s, tenant_id=tenant, run_id=run_b, finding_type=FindingType.ENDPOINT,
                            value=endpoint.value, path="app/api.js", occurrence=occ)

    assert ra.finding_hash == rb.finding_hash
    assert ra.finding_created and rb.finding_created
    assert ra.finding_id != rb.finding_id


def test_findings_are_tenant_isolated(authorized_session):
    tenant_a, session_id = authorized_session
    run_id = _make_run(tenant_a, session_id)
    endpoint = normalize.normalize_endpoint("GET", "/secret-endpoint")
    with tenant_session(tenant_a) as s:
        record_finding(s, tenant_id=tenant_a, run_id=run_id, finding_type=FindingType.ENDPOINT,
                       value=endpoint.value, path="app/api.js",
                       occurrence=Occurrence(raw_url="/secret-endpoint", source_path="app/api.js"))

    tenant_b = sessions_service.create_tenant("intruder-findings")
    with tenant_session(tenant_b) as s:
        # RLS filters tenant A's findings out entirely under tenant B's GUC.
        assert _count(models.Finding)(s) == 0
        assert _count(models.FindingOccurrence)(s) == 0
