import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.events.log import record_event
from recon.findings import normalize, queries, store
from recon.runs import assets
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_TOKEN = "sk_" + "live_" + "SECRETVALUE00"


def _run(tenant, session_id, *, input_ref):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done", input_ref=input_ref)
        session.add(run)
        session.flush()
        return str(run.id)


def _add_secret(tenant, run_id, *, offsets, run_asset_id=None):
    # NOTE: _add_secret must return the finding's `finding_hash` (the identifier
    # `FindingView.finding_hash` is matched against below), not the normalized
    # secret `value` — the two are different SHA-256s (see normalize.finding_hash
    # vs normalize.normalize_secret_value). store.record_finding()'s RecordResult
    # carries the correct one; see the same pattern in queries_triage_test.py.
    value = normalize.normalize_secret_value(_TOKEN, "stripe")
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.SECRET,
            value=value,
            path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js",
                line=1,
                col=0,
                offset_start=offsets[0] if offsets else None,
                offset_end=offsets[1] if offsets else None,
                evidence=_TOKEN,  # a legacy-style plaintext row: must be redacted at read
                engine="kingfisher",
                run_asset_id=run_asset_id,
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
    return result.finding_hash


def test_secret_evidence_redacted_and_revealable_true():
    tenant = sessions_service.create_tenant("rd-1")
    _t, session_id = (
        tenant,
        sessions_service.create_session(
            tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
        ).id,
    )
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


def test_crawl_run_secret_revealable_from_its_own_asset_blob():
    # Slice Y: a crawl run's run.input_ref is NULL — revealable must come from the
    # occurrence's own run_asset.input_ref, not the (absent) run-level blob, and
    # merely being asset-tagged is not enough: an asset still PENDING (no blob
    # fetched yet) must not be revealable either.
    tenant = sessions_service.create_tenant("rd-4")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id

    run_fetched = _run(tenant, session_id, input_ref=None)
    with tenant_session(tenant) as session:
        assets.seed_pending(
            session, tenant_id=tenant, run_id=run_fetched, urls=["https://acme.io/a.js"]
        )
    asset_fetched = assets.list_for_run(tenant, run_fetched)[0]
    with tenant_session(tenant) as session:
        assets.set_fetch_ok(session, asset_fetched.id, f"{tenant}/{run_fetched}/input/deadbeef")
    h1 = _add_secret(tenant, run_fetched, offsets=(10, 30), run_asset_id=asset_fetched.id)
    r1 = queries.list_findings(tenant, run_fetched)
    assert next(f for f in r1.findings if f.finding_hash == h1).revealable is True

    run_pending = _run(tenant, session_id, input_ref=None)
    with tenant_session(tenant) as session:
        assets.seed_pending(
            session, tenant_id=tenant, run_id=run_pending, urls=["https://acme.io/b.js"]
        )
    asset_pending = assets.list_for_run(tenant, run_pending)[0]  # still PENDING: no input_ref
    h2 = _add_secret(tenant, run_pending, offsets=(10, 30), run_asset_id=asset_pending.id)
    r2 = queries.list_findings(tenant, run_pending)
    assert next(f for f in r2.findings if f.finding_hash == h2).revealable is False


def test_endpoint_evidence_is_preserved():
    tenant = sessions_service.create_tenant("rd-3")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id = _run(tenant, session_id, input_ref=None)
    with tenant_session(tenant) as session:
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET /orders",
            path="input.js",
            occurrence=store.Occurrence(
                host="api.acme.io",
                raw_url="/orders",
                evidence='fetch("/orders")',
                engine="vespasian",
            ),
            attributes={"method": "GET", "kind": "fetch"},
        )
    result = queries.list_findings(tenant, run_id)
    endpoint = next(f for f in result.findings if f.type == "endpoint")
    assert endpoint.occurrences[0].evidence == 'fetch("/orders")'
    assert endpoint.revealable is False


def test_occurrence_asset_url_for_crawl_run_and_none_for_legacy():
    # Slice Y (Task 11): the FE needs to know which discovered asset an occurrence
    # came from. asset_url is resolved from the occurrence's run_asset_id ->
    # run_asset.url; a legacy occurrence (no run_asset_id, e.g. a single-file
    # upload run) has no asset to attribute to, so it must read back as None.
    tenant = sessions_service.create_tenant("rd-5")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id

    crawl_run = _run(tenant, session_id, input_ref=None)
    with tenant_session(tenant) as session:
        assets.seed_pending(
            session, tenant_id=tenant, run_id=crawl_run, urls=["https://acme.io/a.js"]
        )
    asset = assets.list_for_run(tenant, crawl_run)[0]
    crawl_hash = _add_secret(tenant, crawl_run, offsets=(10, 30), run_asset_id=asset.id)
    crawl_result = queries.list_findings(tenant, crawl_run)
    crawl_occurrence = next(
        f for f in crawl_result.findings if f.finding_hash == crawl_hash
    ).occurrences[0]
    assert crawl_occurrence.asset_url == "https://acme.io/a.js"

    legacy_run = _run(tenant, session_id, input_ref=f"{tenant}/z/input/legacy")
    legacy_hash = _add_secret(tenant, legacy_run, offsets=(10, 30))
    legacy_result = queries.list_findings(tenant, legacy_run)
    legacy_occurrence = next(
        f for f in legacy_result.findings if f.finding_hash == legacy_hash
    ).occurrences[0]
    assert legacy_occurrence.asset_url is None


def _record_coverage(tenant, run_id, **payload):
    with tenant_session(tenant) as session:
        record_event(
            session,
            tenant_id=tenant,
            run_id=run_id,
            event_type="analyze.coverage",
            payload=payload,
        )


def test_multi_asset_coverage_sums_across_assets_not_last_only():
    # Each asset emits its OWN analyze.coverage event exactly once (a redelivery
    # skips an already analyze-terminal asset, so no asset's event is ever
    # re-emitted) -- the read model must SUM every event for a multi-asset run,
    # not take only the highest-id one, or an earlier asset's secrets vanish
    # behind a later asset's zero-secrets event.
    tenant = sessions_service.create_tenant("rd-6")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id = _run(tenant, session_id, input_ref=None)
    with tenant_session(tenant) as session:
        assets.seed_pending(
            session,
            tenant_id=tenant,
            run_id=run_id,
            urls=["https://acme.io/a.js", "https://acme.io/b.js"],
        )
    _record_coverage(
        tenant,
        run_id,
        attributed=5,
        unattributed=0,
        secrets=2,
        secrets_engine="ok",
        sources_recovered=0,
        source_map="none",
        files=[{"path": "input.js", "attributed": 5, "unattributed": 0}],
    )
    _record_coverage(
        tenant,
        run_id,
        attributed=3,
        unattributed=0,
        secrets=0,
        secrets_engine="ok",
        sources_recovered=0,
        source_map="none",
        files=[{"path": "input.js", "attributed": 3, "unattributed": 0}],
    )

    result = queries.list_findings(tenant, run_id)
    assert result is not None and result.coverage is not None
    assert result.coverage.attributed == 8
    assert result.coverage.secrets == 2  # NOT 0 -- would be if only the last event won


def test_legacy_single_asset_coverage_still_latest_event_wins():
    # A legacy (pre-crawl) run has no run_asset rows -- its one analyze stage is
    # what a stage retry re-runs IN PLACE, re-emitting its single coverage event
    # again, so latest-wins (not summed) is still the correct read there.
    tenant = sessions_service.create_tenant("rd-7")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id = _run(tenant, session_id, input_ref=f"{tenant}/x/input/deadbeef")
    _record_coverage(
        tenant,
        run_id,
        attributed=1,
        unattributed=0,
        secrets=9,
        secrets_engine="ok",
        sources_recovered=0,
        source_map="none",
        files=[],
    )
    _record_coverage(
        tenant,
        run_id,
        attributed=4,
        unattributed=1,
        secrets=0,
        secrets_engine="ok",
        sources_recovered=0,
        source_map="none",
        files=[],
    )

    result = queries.list_findings(tenant, run_id)
    assert result is not None and result.coverage is not None
    assert result.coverage.attributed == 4  # the retry's event alone, not summed
    assert result.coverage.secrets == 0
