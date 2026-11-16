"""The extension's Start screen: find the session already capturing a scope (Continue), and add
a newly seen site to a session's scope."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from recon.db.base import admin_session, tenant_session
from recon.db.models import EngagementSession, Run, RunAsset
from recon.sessions import capture_scope
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _session(tenant, scope, external_id):
    return sessions_service.create_session(
        tenant, name=None, scope_hosts=scope, authorized_by="t", external_id=external_id
    ).id


def test_finds_sessions_whose_scope_holds_the_entry(tenant):
    match = _session(tenant, ["acme.io"], "ext-a")
    _session(tenant, ["beta.example"], "ext-b")

    found = capture_scope.sessions_for_scope(tenant, "*.ACME.io")

    assert [s.session_id for s in found] == [match]
    assert found[0].external_id == "ext-a"
    assert found[0].captures == 0 and found[0].largest is None


def test_reports_the_largest_capture(tenant):
    sid = _session(tenant, ["acme.io"], "ext-a")
    with tenant_session(tenant) as s:
        run = Run(tenant_id=tenant, session_id=sid, state="done")  # a capture: no target/input
        s.add(run)
        s.flush()
        s.add(
            RunAsset(tenant_id=tenant, run_id=run.id, url="https://acme.io/a.js", fetch_status="ok")
        )
    (found,) = capture_scope.sessions_for_scope(tenant, "acme.io")

    assert found.captures == 1
    assert found.largest is not None and found.largest.files == 1


def test_an_invalid_entry_matches_nothing(tenant):
    _session(tenant, ["acme.io"], "ext-a")
    assert capture_scope.sessions_for_scope(tenant, "not a host!") == []


def test_add_scope_entry_is_normalized_and_deduplicated(tenant):
    sid = _session(tenant, ["acme.io"], "ext-a")

    assert capture_scope.add_scope_entry(tenant, sid, "Beta.Example") == ["acme.io", "beta.example"]
    assert capture_scope.add_scope_entry(tenant, sid, "beta.example") == ["acme.io", "beta.example"]


def test_add_scope_entry_rejects_an_invalid_host(tenant):
    sid = _session(tenant, ["acme.io"], "ext-a")
    with pytest.raises(capture_scope.InvalidScopeEntry):
        capture_scope.add_scope_entry(tenant, sid, "*")


def test_only_extension_sessions_can_be_continued(tenant):
    sessions_service.create_session(
        tenant, name="crawl", scope_hosts=["acme.io"], authorized_by="t"
    )
    assert capture_scope.sessions_for_scope(tenant, "acme.io") == []


# --- ADR-0012: Continue adopts a session started in the workspace -----------------------------


def _workspace_session(tenant, scope=("acme.io",), **kw):
    return sessions_service.create_session(
        tenant, name="crawl", scope_hosts=list(scope), authorized_by="t", **kw
    ).id


def _ext_id(tenant, session_id):
    with tenant_session(tenant) as s:
        return s.get(EngagementSession, session_id).external_id


def test_workspace_listing_offers_only_unadopted_sessions_of_the_scope(tenant):
    offered = _workspace_session(tenant)
    _workspace_session(tenant, scope=("beta.example",))
    _session(tenant, ["acme.io"], "ext-a")  # already an extension session
    archived = _workspace_session(tenant)
    sessions_service.set_session_archived(tenant, archived, archived=True)

    found = capture_scope.workspace_sessions_for_scope(tenant, "acme.io")

    assert [w.session_id for w in found] == [offered]
    assert not hasattr(found[0], "external_id")  # there is none to show


def test_adopt_gives_the_session_an_id_and_changes_nothing_else(tenant):
    sid = _workspace_session(tenant)
    before = sessions_service.get_session(tenant, sid)

    with capture_logs() as logs:
        adopted = capture_scope.adopt_workspace_session(tenant, sid, actor="user-1")

    assert adopted.session_id == sid and _ext_id(tenant, sid) == adopted.external_id
    after = sessions_service.get_session(tenant, sid)
    assert (after.engagement_id, after.scope_hosts, after.name, after.created_at) == (
        before.engagement_id,
        before.scope_hosts,
        before.name,
        before.created_at,
    )
    (event,) = [e for e in logs if e["event"] == "session.adopted"]
    assert event["actor"] == "user-1" and event["session_id"] == sid
    assert adopted.external_id not in str(event)  # the id is a capability: never logged
    # now an ordinary Continue match, and no longer offered as a workspace session
    assert [m.session_id for m in capture_scope.sessions_for_scope(tenant, "acme.io")] == [sid]
    assert capture_scope.workspace_sessions_for_scope(tenant, "acme.io") == []


def test_adopt_keeps_the_engagement_binding(tenant):
    from recon.engagements import service as engagements_service

    eng = engagements_service.create_engagement(
        tenant, name="eng", in_scope_domains=["acme.io"], out_of_scope_domains=[]
    ).id
    sid = _workspace_session(tenant, engagement_id=eng)

    adopted = capture_scope.adopt_workspace_session(tenant, sid, actor="u")

    assert adopted.engagement_id == eng
    assert sessions_service.get_session(tenant, sid).engagement_id == eng


def test_a_session_with_an_id_cannot_be_adopted_or_changed(tenant):
    sid = _session(tenant, ["acme.io"], "ext-a")
    with pytest.raises(capture_scope.AdoptRefused) as refused:
        capture_scope.adopt_workspace_session(tenant, sid, actor="u")
    assert refused.value.reason == "already_adopted"
    assert "ext-a" not in str(refused.value)
    assert _ext_id(tenant, sid) == "ext-a"


def test_an_archived_session_cannot_be_adopted(tenant):
    sid = _workspace_session(tenant)
    sessions_service.set_session_archived(tenant, sid, archived=True)
    with pytest.raises(capture_scope.AdoptRefused) as refused:
        capture_scope.adopt_workspace_session(tenant, sid, actor="u")
    assert refused.value.reason == "archived" and _ext_id(tenant, sid) is None


def test_another_tenants_session_is_not_found_and_untouched(tenant):
    other = sessions_service.create_tenant("other-tenant")
    sid = _workspace_session(other)
    with pytest.raises(capture_scope.AdoptRefused) as refused:
        capture_scope.adopt_workspace_session(tenant, sid, actor="u")
    assert refused.value.reason == "not_found" and _ext_id(other, sid) is None
    assert capture_scope.workspace_sessions_for_scope(tenant, "acme.io") == []


def test_unknown_session_is_not_found(tenant):
    with pytest.raises(capture_scope.AdoptRefused) as refused:
        capture_scope.adopt_workspace_session(tenant, str(uuid.uuid4()), actor="u")
    assert refused.value.reason == "not_found"


def test_two_adopts_at_once_have_exactly_one_winner(tenant):
    sid = _workspace_session(tenant)
    barrier = threading.Barrier(6)

    def attempt(_):
        barrier.wait()
        try:
            return capture_scope.adopt_workspace_session(tenant, sid, actor="u").external_id
        except capture_scope.AdoptRefused as exc:
            return exc.reason

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(attempt, range(6)))

    winners = [r for r in results if r != "already_adopted"]
    assert len(winners) == 1 and results.count("already_adopted") == 5
    assert _ext_id(tenant, sid) == winners[0]


def test_the_database_refuses_to_change_or_clear_a_set_id(tenant):
    sid = _session(tenant, ["acme.io"], "ext-a")
    for new in ("ext-b", None):
        with pytest.raises(DBAPIError), admin_session() as db:
            db.execute(
                text("UPDATE session SET external_id = :v WHERE id = :i"), {"v": new, "i": sid}
            )
    assert _ext_id(tenant, sid) == "ext-a"
    free = _workspace_session(tenant)  # NULL -> value is the one allowed move
    with admin_session() as db:
        db.execute(text("UPDATE session SET external_id = 'x' WHERE id = :i"), {"i": free})


def test_continue_counts_capture_runs_not_the_crawl_run(tenant):
    sid = _session(tenant, ["acme.io"], "ext-a")
    with tenant_session(tenant) as s:
        s.add(Run(tenant_id=tenant, session_id=sid, state="done", target="acme.io"))  # a crawl
        s.add(Run(tenant_id=tenant, session_id=sid, state="done"))  # a capture
    (found,) = capture_scope.sessions_for_scope(tenant, "acme.io")
    assert found.captures == 1
