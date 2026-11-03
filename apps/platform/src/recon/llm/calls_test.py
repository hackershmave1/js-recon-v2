"""calls.generate: every LLM call is recorded (what ran, whose key, tokens, time, cost, outcome),
recording never fails the caller, keys never reach storage, and records go with the session."""

import asyncio
import json
import uuid
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from pydantic import BaseModel

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.llm import calls, catalog
from recon.llm.catalog import CatalogModel
from recon.llm.fake_provider import FakeProvider
from recon.llm.provider import LLMOutputError, LLMUsage
from recon.llm.run_choice import RunChoice
from recon.runs import service as runs_service
from recon.sessions import service as sessions_service
from recon.threat_model import service as tm_service

pytestmark = pytest.mark.integration


class _Out(BaseModel):
    ok: bool


def _session(tenant: str) -> str:
    return sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id


def _call(tenant, choice, **kw):
    return asyncio.run(
        calls.generate(
            choice,
            tenant_id=tenant,
            purpose=kw.pop("purpose", "key_test"),
            system_prompt="sys",
            user_prompt=kw.pop("user_prompt", "ping"),
            output_schema=_Out,
            max_tokens=16,
            **kw,
        )
    )


def _rows(tenant: str) -> list[models.LlmCall]:
    with tenant_session(tenant) as db:
        rows = db.query(models.LlmCall).order_by(models.LlmCall.created_at).all()
        db.expunge_all()
        return rows


_TEAM = RunChoice(provider="anthropic", model="claude-x", source="team", api_key="sk-secret-123")


def test_successful_call_is_recorded(tenant, monkeypatch):
    FakeProvider.install(monkeypatch, "anthropic", content={"ok": True})
    response = _call(tenant, _TEAM)
    assert response.content == {"ok": True}
    [row] = _rows(tenant)
    assert (row.purpose, row.provider, row.model, row.key_source, row.status) == (
        "key_test",
        "anthropic",
        "claude-x",
        "team",
        "ok",
    )
    assert (row.prompt_tokens, row.completion_tokens) == (100, 20)
    assert row.latency_ms >= 0
    assert row.prompt_blob_key is None, "no run => no bodies"
    assert row.cost_usd is None, "direct Anthropic has no catalog price: unknown, never a guess"


def test_failed_call_keeps_tokens_and_reraises_the_original(tenant, monkeypatch):
    usage = LLMUsage(prompt_tokens=21480, completion_tokens=12000, total_tokens=33480)
    original = LLMOutputError("hit max_tokens before finishing", usage, "length")
    FakeProvider.install(monkeypatch, "anthropic", error=original)
    with pytest.raises(LLMOutputError) as raised:
        _call(tenant, _TEAM)
    assert raised.value is original
    [row] = _rows(tenant)
    assert (row.status, row.finish_reason, row.prompt_tokens, row.completion_tokens) == (
        "error",
        "length",
        21480,
        12000,
    )
    assert row.error == "hit max_tokens before finishing"


def test_api_key_never_reaches_storage(tenant, monkeypatch):
    FakeProvider.install(
        monkeypatch, "anthropic", error=RuntimeError("bad key sk-secret-123 rejected")
    )
    with pytest.raises(RuntimeError):
        _call(tenant, _TEAM)
    [row] = _rows(tenant)
    assert "sk-secret-123" not in row.error
    assert row.error == "bad key [redacted] rejected"


def test_recording_failure_never_fails_the_call(tenant, monkeypatch):
    FakeProvider.install(monkeypatch, "anthropic", content={"ok": True})

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(calls, "_record", _boom)
    assert _call(tenant, _TEAM).content == {"ok": True}


def test_bodies_stored_under_the_run_and_purged_with_the_session(tenant, redis, monkeypatch):
    FakeProvider.install(monkeypatch, "anthropic", content={"ok": True})
    sid = _session(tenant)
    run = runs_service.create_run(redis, tenant_id=tenant, session_id=sid)
    _call(
        tenant,
        _TEAM,
        purpose="threat_model",
        user_prompt="the recon context",
        session_id=sid,
        run_id=str(run.id),
    )
    [row] = _rows(tenant)
    assert row.prompt_blob_key.startswith(f"{tenant}/{run.id}/llm-prompt/")
    assert json.loads(storage.get_blob(row.prompt_blob_key))["user"] == "the recon context"
    assert json.loads(storage.get_blob(row.response_blob_key)) == {"ok": True}

    assert sessions_service.delete_session(tenant, sid)
    assert _rows(tenant) == [], "records cascade with the session"
    with pytest.raises(ClientError):  # NoSuchKey: the session purge removed the bodies
        storage.get_blob(row.prompt_blob_key)


def test_openrouter_cost_from_cached_catalog_price(tenant, monkeypatch):
    FakeProvider.install(monkeypatch, "openrouter", content={"ok": True})
    choice = RunChoice(provider="openrouter", model="vendor/m:floor", source="env", api_key="k")
    catalog.reset()
    _call(tenant, choice)  # cold cache: unknown
    priced = CatalogModel(
        id="vendor/m",
        name="m",
        context_length=1,
        max_completion_tokens=None,
        prompt_price=Decimal("0.000002"),
        completion_price=Decimal("0.00001"),
    )
    monkeypatch.setitem(catalog._state, "models", [priced])
    _call(tenant, choice)
    cold, warm = _rows(tenant)
    assert cold.cost_usd is None
    assert warm.cost_usd == Decimal("0.000400"), (
        "100*0.000002 + 20*0.00001; :floor bills as the base model"
    )
    catalog.reset()


def test_records_are_tenant_isolated(tenant, monkeypatch):
    FakeProvider.install(monkeypatch, "anthropic", content={"ok": True})
    _call(tenant, _TEAM)
    other = sessions_service.create_tenant(f"other-{uuid.uuid4().hex[:6]}")
    assert len(_rows(tenant)) == 1
    assert _rows(other) == []


# --- the Threat Model tab's run-details line -------------------------------------------------


def _threat_output() -> dict:
    return {"analysis_summary": "s", "threats": []}


def _run_threat_model(tenant, sid):
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid))
    return tm_service.get_threat_model(tenant, sid)


def test_threat_model_shows_its_own_call(tenant, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    FakeProvider.install(monkeypatch, "anthropic", content=_threat_output())
    monkeypatch.setattr(
        tm_service, "_assemble_context", lambda t, s: tm_service._Context("ctx", frozenset(), None)
    )
    sid = _session(tenant)
    state = _run_threat_model(tenant, sid)
    assert state["status"] == "done"
    assert state["last_call"]["key_source"] == "env"
    assert (state["last_call"]["prompt_tokens"], state["last_call"]["completion_tokens"]) == (
        100,
        20,
    )

    # A re-run that fails BEFORE calling the LLM must not show the previous run's numbers.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    state = _run_threat_model(tenant, sid)
    assert state["status"] == "failed"
    assert state["last_call"] is None


def test_threat_model_call_record_lost_shows_unavailable(tenant, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    FakeProvider.install(monkeypatch, "anthropic", content=_threat_output())
    monkeypatch.setattr(
        tm_service, "_assemble_context", lambda t, s: tm_service._Context("ctx", frozenset(), None)
    )
    monkeypatch.setattr(
        calls, "_record", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("s3 down"))
    )
    state = _run_threat_model(tenant, _session(tenant))
    assert state["status"] == "done", "losing the record never costs the user the result"
    assert state["last_call"] == {"unavailable": True}


def test_missing_usage_is_unknown_not_free(tenant, monkeypatch):
    # An OpenRouter reply without usage must read "cost unknown", never "0 tokens · $0.00".
    unreported = LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0, reported=False)
    FakeProvider.install(monkeypatch, "openrouter", content={"ok": True}, usage=unreported)
    priced = CatalogModel(
        id="vendor/m",
        name="m",
        context_length=1,
        max_completion_tokens=None,
        prompt_price=Decimal("0.000002"),
        completion_price=Decimal("0.00001"),
    )
    monkeypatch.setitem(catalog._state, "models", [priced])
    _call(tenant, RunChoice(provider="openrouter", model="vendor/m", source="env", api_key="k"))
    [row] = _rows(tenant)
    assert (row.prompt_tokens, row.completion_tokens, row.cost_usd) == (None, None, None)
    catalog.reset()
