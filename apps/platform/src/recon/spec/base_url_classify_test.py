import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.spec import service

pytestmark = pytest.mark.integration

_SPEC = (
    b'{"openapi":"3.0.3","info":{"title":"t","version":"0"},'
    b'"paths":{"/location/address/search":{"get":{"responses":{"default":{"description":"x"}}}}}}'
)


def _run_with_relative_endpoint(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        # A RELATIVE endpoint (no occurrence host) — its base lives in another file.
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /address/search",
            path="app.js",
            occurrence=store.Occurrence(host=None, raw_url="/address/search"),
            attributes={"method": "GET", "kind": "fetch"},
            first_stage="analyzing",
        )
        return run_id


def _status(tenant, session_id, value="GET /address/search"):
    from recon.findings.normalize import finding_hash

    h = finding_hash("endpoint", value)
    with tenant_session(tenant) as session:
        row = (
            session.query(models.FindingSpecStatus)
            .filter_by(
                session_id=session_id,
                finding_hash=h,
            )
            .one()
        )
        return row.status


def test_set_base_flips_unresolved_to_documented(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_relative_endpoint(tenant, session_id)
    # Attach the spec: /address/search is a suffix of /location/address/search -> unresolved.
    service.attach_and_classify(tenant, run_id, _SPEC)
    assert _status(tenant, session_id) == "unresolved"
    # Add a base rule and reclassify -> documented.
    with tenant_session(tenant) as session:
        session.add(
            models.SessionBaseUrl(
                tenant_id=tenant,
                session_id=session_id,
                kind="prefix",
                path_prefix="/address",
                base_url="/location",
            )
        )
    service.reclassify_run(tenant, run_id)
    assert _status(tenant, session_id) == "documented"


def test_absolute_op_stays_documented_under_broad_prefix(authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        # An ABSOLUTE endpoint (occurrence has a host) already documented as /location/....
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /location/address/search",
            path="app.js",
            occurrence=store.Occurrence(
                host="acme.io", raw_url="https://acme.io/location/address/search"
            ),
            attributes={"method": "GET", "kind": "fetch"},
            first_stage="analyzing",
        )
    service.attach_and_classify(tenant, run_id, _SPEC)
    assert _status(tenant, session_id, "GET /location/address/search") == "documented"
    # A broad prefix that WOULD double-prepend if the host-gate were missing (gate B1).
    with tenant_session(tenant) as session:
        session.add(
            models.SessionBaseUrl(
                tenant_id=tenant,
                session_id=session_id,
                kind="prefix",
                path_prefix="/location",
                base_url="/x",
            )
        )
    service.reclassify_run(tenant, run_id)
    assert _status(tenant, session_id, "GET /location/address/search") == "documented"


def test_mixed_relative_absolute_op_not_rebased_matches_reconstruct(authorized_session):
    # Same op value in two files: a.js relative (host-less hash) + b.js absolute
    # (host-bearing hash). REQ-C2 option B (reconcile the classify vs reconstruct
    # base-gate granularity): because the OPERATION is observed absolute ANYWHERE,
    # classify skips re-basing BOTH hashes ("observed absolute beats the prefix
    # guess"). The two hashes therefore converge, and — the point of the reconcile —
    # the shadow verdict matches what reconstruct (op-group host gate) exports: the
    # observed /address/search path, never the /location prefix guess.
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for src, host, raw in [
            ("a.js", None, "/address/search"),
            ("b.js", "acme.io", "https://acme.io/address/search"),
        ]:
            store.record_finding(
                session,
                tenant_id=tenant,
                run_id=run_id,
                finding_type=FindingType.ENDPOINT,
                value="GET /address/search",
                path=src,
                occurrence=store.Occurrence(host=host, raw_url=raw),
                attributes={"method": "GET", "kind": "fetch"},
                first_stage="analyzing",
            )
    service.attach_and_classify(tenant, run_id, _SPEC)
    with tenant_session(tenant) as session:
        session.add(
            models.SessionBaseUrl(
                tenant_id=tenant,
                session_id=session_id,
                kind="prefix",
                path_prefix="/address",
                base_url="/location",
            )
        )
    service.reclassify_run(tenant, run_id)

    from recon.findings.normalize import finding_hash

    h_rel = finding_hash("endpoint", "GET /address/search")
    h_abs = finding_hash("endpoint", "GET /address/search")
    with tenant_session(tenant) as session:
        status = {
            r.finding_hash: r.status
            for r in session.query(models.FindingSpecStatus).filter_by(session_id=session_id).all()
        }
    # Both converge: the op was seen absolute, so neither hash is re-based ->
    # /address/search is a suffix of the documented /location/address/search.
    assert status[h_rel] == "unresolved"
    assert status[h_abs] == "unresolved"

    # Parity with reconstruct: its op-group host gate also skips the re-base, so the
    # assembled request keeps the observed path, never gaining a /location prefix.
    from recon.probe.reconstruct import reconstruct_run

    operations = {r.operation for r in reconstruct_run(tenant, run_id)}
    assert "GET /address/search" in operations
    assert not any(op.startswith("GET /location") for op in operations)


def test_selection_rule_on_relative_hash_overridden_when_op_seen_absolute(authorized_session):
    # The subtlest B1 consequence: an explicit SELECTION rule targeting the relative
    # hash is IGNORED when the SAME operation was observed absolute elsewhere — the
    # per-operation host gate short-circuits before rule matching (base_url.py:103),
    # exactly as reconstruct's bool(hosts) gate does. So "observed absolute beats the
    # prefix guess" extends to selection rules too (parity, not just prefix rules).
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for src, host, raw in [
            ("a.js", None, "/address/search"),
            ("b.js", "acme.io", "https://acme.io/address/search"),
        ]:
            store.record_finding(
                session,
                tenant_id=tenant,
                run_id=run_id,
                finding_type=FindingType.ENDPOINT,
                value="GET /address/search",
                path=src,
                occurrence=store.Occurrence(host=host, raw_url=raw),
                attributes={"method": "GET", "kind": "fetch"},
                first_stage="analyzing",
            )
    service.attach_and_classify(tenant, run_id, _SPEC)

    from recon.findings.normalize import finding_hash

    h_rel = finding_hash("endpoint", "GET /address/search")
    with tenant_session(tenant) as session:
        session.add(
            models.SessionBaseUrl(
                tenant_id=tenant,
                session_id=session_id,
                kind="selection",
                finding_hashes=[h_rel],
                base_url="/location",
            )
        )
    service.reclassify_run(tenant, run_id)
    with tenant_session(tenant) as session:
        status = {
            r.finding_hash: r.status
            for r in session.query(models.FindingSpecStatus).filter_by(session_id=session_id).all()
        }
    # Selection ignored (op seen absolute) -> not re-based -> unresolved, matching export.
    assert status[h_rel] == "unresolved"
    from recon.probe.reconstruct import reconstruct_run

    assert not any(r.operation.startswith("GET /location") for r in reconstruct_run(tenant, run_id))


def test_capture_resolved_string_param_stays_documented_not_false_shadow(authorized_session):
    # REQ-C3 (§4 code-review must-fix #1): a capture occurrence proves the base, so classify
    # strips the leading ${var} but KEEPS ${slug} templated -> a documented string-param
    # endpoint stays documented, never false-shadowed by the concrete runtime value
    # (/users/alice). Feeding the concrete path (the bug) would flip this to shadow.
    tenant, session_id = authorized_session
    value = "GET /${apiBase}/users/${slug}"
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for occurrence in (
            store.Occurrence(host=None, raw_url="${apiBase}/users/${slug}", engine="vespasian"),
            store.Occurrence(
                host="api.acme.io", raw_url="https://api.acme.io/users/alice", engine="capture"
            ),
        ):
            store.record_finding(
                session,
                tenant_id=tenant,
                run_id=run_id,
                finding_type=FindingType.ENDPOINT,
                value=value,
                path="app.js",
                occurrence=occurrence,
                attributes={"method": "GET", "kind": "fetch"},
                first_stage="analyzing",
            )
    spec = (
        b'{"openapi":"3.0.3","info":{"title":"t","version":"0"},'
        b'"paths":{"/users/{id}":{"get":{'
        b'"parameters":[{"name":"id","in":"path","required":true,"schema":{"type":"string"}}],'
        b'"responses":{"default":{"description":"x"}}}}}}'
    )
    service.attach_and_classify(tenant, run_id, spec)
    assert _status(tenant, session_id, value=value) == "documented"
