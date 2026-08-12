"""Slice 4 — cross-run "sightings" overlay on ``list_findings``.

The same JS reached by a platform crawl AND a browser-extension capture produces
duplicate ``Finding`` rows in two different runs; REQ-D5 forbids merging them, so
the read model attaches, per finding, a count of the OTHER runs in the same
engagement that share its ``finding_hash`` — bucketed by origin (``capture`` =
extension, ``platform`` = crawl/upload). Engagement-scoped so an unrelated target
that happens to collide on ``finding_hash`` is never cross-linked; ``None`` when
the run's session has no engagement (ungrouped), distinct from an all-zero summary
(grouped but unique).
"""

import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.engagements import service as engagements_service
from recon.findings import queries, store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _engagement(tenant: str) -> str:
    return engagements_service.create_engagement(
        tenant, name="eng", in_scope_domains=["acme.io"], out_of_scope_domains=[]
    ).id


def _session(tenant: str, *, engagement_id: str | None, external_id: str | None) -> str:
    return sessions_service.create_session(
        tenant,
        name="s",
        scope_hosts=["acme.io"],
        authorized_by="t",
        engagement_id=engagement_id,
        external_id=external_id,
    ).id


def _run_with_endpoint(tenant: str, session_id: str, *, input_ref: str | None = None) -> str:
    """A ``done`` run in ``session_id`` carrying the one shared endpoint finding.
    ``input_ref`` set == an uploaded bundle (the upload origin); left NULL == a
    crawl or an extension-capture accumulator run."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done", input_ref=input_ref)
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /orders",
            path="input.js",
            occurrence=store.Occurrence(host="api.acme.io", raw_url="/orders"),
            attributes={"method": "GET", "kind": "fetch"},
            first_stage="analyzing",
        )
        return run_id


def _run_with_two_endpoints(
    tenant: str, session_id: str, *, input_ref: str | None = None
) -> tuple[str, dict[str, str]]:
    """A ``done`` run carrying TWO distinct findings (different finding_hash), so a
    fan-out test can prove the per-hash buckets don't cross-contaminate."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done", input_ref=input_ref)
        session.add(run)
        session.flush()
        run_id = str(run.id)
        hashes: dict[str, str] = {}
        for value, raw in (("GET /orders", "/orders"), ("GET /users", "/users")):
            result = store.record_finding(
                session,
                tenant_id=tenant,
                run_id=run_id,
                finding_type=FindingType.ENDPOINT,
                value=value,
                path="input.js",
                occurrence=store.Occurrence(host="api.acme.io", raw_url=raw),
                attributes={"method": "GET", "kind": "fetch"},
                first_stage="analyzing",
            )
            hashes[value] = result.finding_hash
        return run_id, hashes


def _only(view) -> object:
    assert view is not None
    assert len(view.findings) == 1
    return view.findings[0]


def test_sightings_bucket_other_runs_by_origin_within_engagement():
    tenant = sessions_service.create_tenant("sight-origin")
    engagement = _engagement(tenant)
    crawl_session = _session(tenant, engagement_id=engagement, external_id=None)
    capture_session = _session(tenant, engagement_id=engagement, external_id="ext-cap-1")

    crawl_run = _run_with_endpoint(tenant, crawl_session)  # platform origin
    capture_run = _run_with_endpoint(tenant, capture_session)  # capture origin

    # From the crawl run's finding: the one other sighting is the capture run.
    from_crawl = _only(queries.list_findings(tenant, crawl_run))
    assert from_crawl.sightings is not None
    assert from_crawl.sightings.capture == 1
    assert from_crawl.sightings.platform == 0

    # Symmetric: from the capture run's finding, the crawl run is a platform sighting.
    from_capture = _only(queries.list_findings(tenant, capture_run))
    assert from_capture.sightings is not None
    assert from_capture.sightings.capture == 0
    assert from_capture.sightings.platform == 1


def test_upload_into_a_capture_session_counts_as_platform_not_capture():
    # Regression: origin is derived from session.external_id, but a capture session
    # (external_id set, authorization_ack True) can host an /runs/upload run. Such a
    # run must read as `platform` — the `run.input_ref IS NULL` guard is what
    # distinguishes an extension accumulator (NULL) from an upload (set).
    tenant = sessions_service.create_tenant("sight-upload")
    engagement = _engagement(tenant)
    capture_session = _session(tenant, engagement_id=engagement, external_id="ext-cap-2")
    crawl_session = _session(tenant, engagement_id=engagement, external_id=None)

    _run_with_endpoint(tenant, capture_session, input_ref="tenant/run/js/deadbeef")  # upload
    crawl_run = _run_with_endpoint(tenant, crawl_session)

    finding = _only(queries.list_findings(tenant, crawl_run))
    assert finding.sightings is not None
    assert finding.sightings.capture == 0, "an upload in a capture session is not a capture"
    assert finding.sightings.platform == 1


def test_ungrouped_run_reads_sightings_as_none():
    # engagement_id IS NULL -> the cross-run query is skipped entirely (never
    # `WHERE engagement_id = NULL`); sightings is None ("ungrouped"), even though a
    # duplicate exists in another ungrouped session — we won't collapse across
    # unrelated targets. This is the tri-state the FE keys its "group to enable" hint on.
    tenant = sessions_service.create_tenant("sight-ungrouped")
    session_a = _session(tenant, engagement_id=None, external_id=None)
    session_b = _session(tenant, engagement_id=None, external_id="ext-cap-3")
    run_a = _run_with_endpoint(tenant, session_a)
    _run_with_endpoint(tenant, session_b)  # a real duplicate, but ungrouped

    finding = _only(queries.list_findings(tenant, run_a))
    assert finding.sightings is None


def test_grouped_but_unique_finding_reads_zero_not_none():
    # Grouped under an engagement but no other run shares the hash: a real all-zero
    # summary, distinct from None. Also proves the current run never counts itself.
    tenant = sessions_service.create_tenant("sight-unique")
    engagement = _engagement(tenant)
    only_session = _session(tenant, engagement_id=engagement, external_id=None)
    run = _run_with_endpoint(tenant, only_session)

    finding = _only(queries.list_findings(tenant, run))
    assert finding.sightings is not None
    assert finding.sightings.capture == 0
    assert finding.sightings.platform == 0


def test_sightings_do_not_cross_engagements():
    # The same finding_hash under a DIFFERENT engagement is not a sighting — the
    # whole point of engagement-scoping (finding_hash carries no app identity).
    tenant = sessions_service.create_tenant("sight-isolation")
    engagement_one = _engagement(tenant)
    engagement_two = _engagement(tenant)
    session_one = _session(tenant, engagement_id=engagement_one, external_id=None)
    session_two = _session(tenant, engagement_id=engagement_two, external_id="ext-cap-4")
    run_one = _run_with_endpoint(tenant, session_one)
    _run_with_endpoint(tenant, session_two)  # same hash, other engagement

    finding = _only(queries.list_findings(tenant, run_one))
    assert finding.sightings is not None
    assert finding.sightings.capture == 0
    assert finding.sightings.platform == 0


def test_sightings_never_cross_tenants():
    # Tenant isolation is the load-bearing security property here and rests entirely
    # on RLS; guard it so a future refactor (a raw/admin query, a dropped
    # tenant_session) can't silently leak a cross-tenant sighting. The same
    # finding_hash under two tenants -> zero counts, never each other.
    tenant_a = sessions_service.create_tenant("sight-tenant-a")
    tenant_b = sessions_service.create_tenant("sight-tenant-b")
    session_a = _session(tenant_a, engagement_id=_engagement(tenant_a), external_id=None)
    session_b = _session(tenant_b, engagement_id=_engagement(tenant_b), external_id="ext-cap-b")
    run_a = _run_with_endpoint(tenant_a, session_a)
    _run_with_endpoint(tenant_b, session_b)  # same finding_hash, other tenant

    finding = _only(queries.list_findings(tenant_a, run_a))
    assert finding.sightings is not None
    assert finding.sightings.capture == 0
    assert finding.sightings.platform == 0


def test_sightings_fan_out_buckets_both_origins_per_hash():
    # Three other runs share each hash: one capture + two platform. From the viewing
    # run, each of its two DISTINCT findings must read capture=1, platform=2 -- proving
    # both buckets populate at once AND the two hashes don't cross-contaminate.
    tenant = sessions_service.create_tenant("sight-fanout")
    engagement = _engagement(tenant)
    view_session = _session(tenant, engagement_id=engagement, external_id=None)
    capture_session = _session(tenant, engagement_id=engagement, external_id="ext-cap-6")
    crawl_session = _session(tenant, engagement_id=engagement, external_id=None)

    view_run, _hashes = _run_with_two_endpoints(tenant, view_session)  # self, excluded
    _run_with_two_endpoints(tenant, capture_session)  # capture origin
    _run_with_two_endpoints(tenant, crawl_session)  # platform origin
    _run_with_two_endpoints(tenant, crawl_session)  # platform origin (2nd run, same session)

    view = queries.list_findings(tenant, view_run)
    assert view is not None
    assert len(view.findings) == 2
    for finding in view.findings:
        assert finding.sightings is not None, finding.value
        assert finding.sightings.capture == 1, finding.value
        assert finding.sightings.platform == 2, finding.value
