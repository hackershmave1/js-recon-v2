"""Hosts a standard crawl REFERENCED but did not fetch (ADR-0010, K1): listed as undecided with
no held files, Add puts the host in scope and names the run to re-crawl, Ignore is final."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from recon.config import get_settings
from recon.db import models
from recon.db.base import tenant_session
from recon.quarantine import service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

CDN = "cdn.acme-static.com"
CDN_JS = f"https://{CDN}/lib.js"


@pytest.fixture()
def world():
    tenant = sessions_service.create_tenant(f"referenced-{uuid.uuid4().hex[:8]}")
    view = sessions_service.create_session(
        tenant, name="k1", scope_hosts=["acme.io"], authorized_by="t"
    )
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        return tenant, view.id, str(run.id)


def _note(world, host=CDN, urls=None):
    tenant, session_id, run_id = world
    return service.note_referenced(
        tenant_id=tenant,
        session_id=session_id,
        run_id=run_id,
        urls_by_host={host: urls or [f"https://{host}/lib.js"]},
    )


def _scope(tenant, session_id):
    with tenant_session(tenant) as session:
        return list(
            session.scalar(
                select(models.EngagementSession.scope_hosts).where(
                    models.EngagementSession.id == session_id
                )
            )
        )


def test_a_referenced_host_is_listed_with_no_held_files(world):
    tenant, session_id, run_id = world
    assert _note(world) == [CDN]
    [row] = service.undecided_hosts(tenant_id=tenant, session_id=session_id)
    assert row["host"] == CDN
    assert row["files"] == 0 and row["bytes"] == 0
    assert row["referenced"] is True
    assert row["referenced_urls"] == [CDN_JS]
    assert _scope(tenant, session_id) == ["acme.io"]  # listing changes nothing in the scope


def test_noting_twice_lists_once_and_merges_urls(world):
    tenant, session_id, _ = world
    _note(world)
    assert _note(world, urls=[f"https://{CDN}/other.js", CDN_JS]) == []  # already listed
    [row] = service.undecided_hosts(tenant_id=tenant, session_id=session_id)
    assert row["referenced_urls"] == [CDN_JS, f"https://{CDN}/other.js"]


def test_add_joins_the_scope_and_names_the_run_to_recrawl(world):
    tenant, session_id, run_id = world
    _note(world)
    result = service.decide(tenant_id=tenant, session_id=session_id, host=CDN, decision="add")
    assert (result.status, result.state) == ("ok", "added")
    assert result.recrawl_run_id == run_id
    assert CDN in _scope(tenant, session_id)
    assert service.undecided_hosts(tenant_id=tenant, session_id=session_id) == []


def test_a_second_add_does_not_ask_for_another_recrawl(world):
    tenant, session_id, _ = world
    _note(world)
    service.decide(tenant_id=tenant, session_id=session_id, host=CDN, decision="add")
    again = service.decide(tenant_id=tenant, session_id=session_id, host=CDN, decision="add")
    assert again.status == "ok" and again.recrawl_run_id is None


def test_ignore_is_final_and_nothing_is_fetched(world):
    tenant, session_id, _ = world
    _note(world)
    result = service.decide(tenant_id=tenant, session_id=session_id, host=CDN, decision="ignore")
    assert result.state == "ignored" and result.recrawl_run_id is None
    assert _scope(tenant, session_id) == ["acme.io"]
    # a later crawl that sees the same host does not list it again
    assert _note(world) == []
    assert service.undecided_hosts(tenant_id=tenant, session_id=session_id) == []
    assert service.ignored_hosts(tenant_id=tenant, session_id=session_id) == [CDN]


def test_an_added_host_is_not_listed_again(world):
    tenant, session_id, _ = world
    _note(world)
    service.decide(tenant_id=tenant, session_id=session_id, host=CDN, decision="add")
    assert _note(world) == []


def test_the_host_cap_applies_to_referenced_hosts(world, monkeypatch):
    patched = get_settings().model_copy(update={"quarantine_max_hosts_per_session": 1})
    monkeypatch.setattr(service, "get_settings", lambda: patched)
    tenant, session_id, run_id = world
    listed = service.note_referenced(
        tenant_id=tenant,
        session_id=session_id,
        run_id=run_id,
        urls_by_host={"a.acme-static.com": ["https://a.acme-static.com/a.js"], CDN: [CDN_JS]},
    )
    assert listed == ["a.acme-static.com"]


def test_a_run_of_another_session_is_refused(world):
    tenant, session_id, _ = world
    other = sessions_service.create_session(tenant, name="o", scope_hosts=[], authorized_by="t")
    with tenant_session(tenant) as session:
        foreign = models.Run(tenant_id=tenant, session_id=other.id, state="done")
        session.add(foreign)
        session.flush()
        foreign_id = str(foreign.id)
    with pytest.raises(service.QuarantineError):
        service.note_referenced(
            tenant_id=tenant,
            session_id=session_id,
            run_id=foreign_id,
            urls_by_host={CDN: [CDN_JS]},
        )
