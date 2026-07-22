import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import normalize, queries, store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_TOKEN = "sk_" + "live_" + "SECRETVALUE00"


def _run(tenant, session_id, *, input_ref):
    with tenant_session(tenant) as session:
        run = models.Run(
            tenant_id=tenant, session_id=session_id, state="done", input_ref=input_ref
        )
        session.add(run)
        session.flush()
        return str(run.id)


def _add_secret(tenant, run_id, *, offsets):
    # NOTE: _add_secret must return the finding's `finding_hash` (the identifier
    # `FindingView.finding_hash` is matched against below), not the normalized
    # secret `value` — the two are different SHA-256s (see normalize.finding_hash
    # vs normalize.normalize_secret_value). store.record_finding()'s RecordResult
    # carries the correct one; see the same pattern in queries_triage_test.py.
    value = normalize.normalize_secret_value(_TOKEN, "stripe")
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=1, col=0,
                offset_start=offsets[0] if offsets else None,
                offset_end=offsets[1] if offsets else None,
                evidence=_TOKEN,  # a legacy-style plaintext row: must be redacted at read
                engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
    return result.finding_hash


def test_secret_evidence_redacted_and_revealable_true():
    tenant = sessions_service.create_tenant("rd-1")
    _t, session_id = tenant, sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id = _run(tenant, session_id, input_ref=f"{tenant}/x/input/deadbeef")
    secret_hash = _add_secret(tenant, run_id, offsets=(10, 30))

    result = queries.list_findings(tenant, run_id)
    secret = next(f for f in result.findings if f.finding_hash == secret_hash)
    assert secret.revealable is True
    assert all(o.evidence is None for o in secret.occurrences)  # redacted at read


def test_secret_not_revealable_without_offsets_or_blob():
    tenant = sessions_service.create_tenant("rd-2")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id

    run_no_blob = _run(tenant, session_id, input_ref=None)
    h1 = _add_secret(tenant, run_no_blob, offsets=(10, 30))
    r1 = queries.list_findings(tenant, run_no_blob)
    assert next(f for f in r1.findings if f.finding_hash == h1).revealable is False

    run_no_offsets = _run(tenant, session_id, input_ref=f"{tenant}/y/input/beef")
    h2 = _add_secret(tenant, run_no_offsets, offsets=None)
    r2 = queries.list_findings(tenant, run_no_offsets)
    assert next(f for f in r2.findings if f.finding_hash == h2).revealable is False


def test_endpoint_evidence_is_preserved():
    tenant = sessions_service.create_tenant("rd-3")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id = _run(tenant, session_id, input_ref=None)
    with tenant_session(tenant) as session:
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /orders", path="input.js",
            occurrence=store.Occurrence(
                host="api.acme.io", raw_url="/orders",
                evidence='fetch("/orders")', engine="vespasian",
            ),
            attributes={"method": "GET", "kind": "fetch"},
        )
    result = queries.list_findings(tenant, run_id)
    endpoint = next(f for f in result.findings if f.type == "endpoint")
    assert endpoint.occurrences[0].evidence == 'fetch("/orders")'
    assert endpoint.revealable is False
