"""The default-visibility overlay end to end: one stored run, every surface agrees (Postgres)."""

import uuid

import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.fetch import egress
from recon.findings import hosts, queries, store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_ROUTES = ["/dashboard", "https://acme.io/login", "https://x", "https://github.com/lib"]


def _run(tenant: str, session_id: str) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        for value in _ROUTES:
            store.record_finding(
                session,
                tenant_id=tenant,
                run_id=run_id,
                finding_type=FindingType.PAGE_ROUTE,
                value=value,
                path="input.js",
                occurrence=store.Occurrence(
                    host=egress.attributed_host(value), source_path="input.js"
                ),
                first_stage="analyzing",
            )
        store.record_finding(
            session,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.ENDPOINT,
            value="GET https://api.mixpanel.com/track",
            path="input.js",
            occurrence=store.Occurrence(host="api.mixpanel.com", source_path="input.js"),
            first_stage="analyzing",
        )
        return run_id


@pytest.fixture()
def run():
    tenant = sessions_service.create_tenant(f"visibility-{uuid.uuid4().hex[:8]}")
    session_view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    return tenant, session_view.id, _run(tenant, session_view.id)


def test_the_list_shows_only_meaningful_findings_and_says_what_it_held_back(run):
    tenant, _, run_id = run
    view = queries.list_findings(tenant, run_id)
    assert {f.value for f in view.findings} == {"/dashboard", "https://acme.io/login"}
    assert view.total == 2
    assert view.hidden == {"junk_or_dynamic": 1, "off_scope": 1, "noise": 1}
    everything = queries.list_findings(tenant, run_id, include_hidden=True)
    assert len(everything.findings) == 5  # captured, never deleted


def test_counts_hosts_and_the_popup_agree_with_the_list(run):
    tenant, session_id, run_id = run
    with tenant_session(tenant) as session:
        assert queries.visible_type_counts(session, run_id) == {"page_route": 2}
    summary = queries.get_session_findings_summary(tenant, session_id)
    assert summary.counts.total == 2
    assert {f.value for f in summary.top_findings} <= {"/dashboard", "https://acme.io/login"}
    inventory = {h.host for h in hosts.list_hosts(tenant, run_id).hosts}
    assert "github.com" not in inventory and "acme.io" in inventory
    assert "github.com" in {
        h.host for h in hosts.list_hosts(tenant, run_id, include_noise=True).hosts
    }


def test_a_run_diff_ignores_hidden_findings(run):
    tenant, session_id, run_id = run
    other = _run(tenant, session_id)
    diff = queries.diff_runs(tenant, other, run_id)
    assert {e.value for e in diff.persisted} == {"/dashboard", "https://acme.io/login"}


def _endpoint_run(tenant: str, session_id: str, value: str, *, as_v2: bool) -> str:
    """A run holding one endpoint; ``as_v2`` stores the hash a pre-v3 build wrote."""
    import hashlib
    import json

    from sqlalchemy import update

    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        stored = store.record_finding(
            session,
            tenant_id=tenant,
            run_id=str(run.id),
            finding_type=FindingType.ENDPOINT,
            value=value,
            path="input.js",
            occurrence=store.Occurrence(host="acme.io", source_path="input.js"),
            first_stage="analyzing",
        )
        if as_v2:
            canonical = {"type": "endpoint", "v": 2, "value": value}
            v2 = hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            session.execute(
                update(models.Finding)
                .where(models.Finding.id == stored.finding_id)
                .values(finding_hash=v2)
            )
        return str(run.id)


def test_a_diff_against_a_pre_v3_run_compares_like_with_like(run):
    # Identity v3: the base run stored `GET /u/${userId}` under v2; the new run stores
    # `GET /u/{id}`. Same endpoint -> persisted, not "1 gone + 1 new".
    tenant, session_id, _ = run
    base = _endpoint_run(tenant, session_id, "GET /u/${userId}", as_v2=True)
    current = _endpoint_run(tenant, session_id, "GET /u/{id}", as_v2=False)
    diff = queries.diff_runs(tenant, current, base)
    assert [e.value for e in diff.persisted] == ["GET /u/{id}"]
    assert diff.new == [] and diff.gone == []
    assert diff.identity_upgraded is True
    same_version = queries.diff_runs(tenant, current, current)
    assert same_version.identity_upgraded is False
