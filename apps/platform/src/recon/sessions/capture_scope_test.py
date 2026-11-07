"""The extension's Start screen: find the session already capturing a scope (Continue), and add
a newly seen site to a session's scope."""

import pytest

from recon.db.base import tenant_session
from recon.db.models import Run
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
        s.add(Run(tenant_id=tenant, session_id=sid, state="done", input_ref="x"))

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
