"""run_generation must fail loudly, never stay 'running', when the stored key can't be decrypted."""

import asyncio
import uuid

import pytest

from recon.auth import service as auth_service
from recon.llm import service as llm_service
from recon.llm import tenant_config
from recon.llm.crypto import KeyDecryptError
from recon.sessions import service as sessions_service
from recon.threat_model import service as tm_service

pytestmark = pytest.mark.integration


class _LogRecorder:
    """Stands in for the module logger: structlog caches loggers on first use, so
    structlog.testing.capture_logs can miss them."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __getattr__(self, _level):
        return lambda event, **kw: self.events.append((event, kw))


def _session(tenant: str) -> str:
    return sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id


def test_undecryptable_key_marks_failed(tenant, monkeypatch):
    sid = _session(tenant)
    tm_service.trigger_generation(tenant, sid)
    monkeypatch.setattr(tm_service, "_assemble_context", lambda t, s: ("ctx", frozenset()))

    def _boom(t, s):
        raise KeyDecryptError("stored LLM key could not be decrypted")

    monkeypatch.setattr(llm_service, "load_credentials", _boom)
    recorder = _LogRecorder()
    monkeypatch.setattr(tm_service, "log", recorder)
    asyncio.run(tm_service.run_generation(tenant, sid))
    state = tm_service.get_threat_model(tenant, sid)
    assert state["status"] == "failed"
    assert state["error"] == "stored LLM key could not be decrypted; re-save it"
    fields = dict(recorder.events)["llm.credentials.decrypt_failed"]
    assert fields["tenant_id"] == tenant
    assert "exc_info" not in fields  # a traceback could carry ciphertext


def test_credential_load_crash_marks_failed_and_logs_traceback(tenant, monkeypatch):
    sid = _session(tenant)
    tm_service.trigger_generation(tenant, sid)
    monkeypatch.setattr(tm_service, "_assemble_context", lambda t, s: ("ctx", frozenset()))

    def _boom(t, s):
        raise RuntimeError("db went away")

    monkeypatch.setattr(llm_service, "load_credentials", _boom)
    recorder = _LogRecorder()
    monkeypatch.setattr(tm_service, "log", recorder)
    asyncio.run(tm_service.run_generation(tenant, sid))
    assert tm_service.get_threat_model(tenant, sid)["error"] == "could not load LLM credentials"
    fields = dict(recorder.events)["llm.credentials.load_failed"]
    assert fields["tenant_id"] == tenant
    assert fields["exc_info"] is True


def test_no_key_error_names_the_team_settings(tenant, monkeypatch):
    for env_var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    sid = _session(tenant)
    tm_service.trigger_generation(tenant, sid)
    monkeypatch.setattr(tm_service, "_assemble_context", lambda t, s: ("ctx", frozenset()))
    asyncio.run(tm_service.run_generation(tenant, sid))
    error = tm_service.get_threat_model(tenant, sid)["error"]
    assert error.startswith("no LLM API key")  # the UI's Settings link matches this prefix
    assert "ask an admin to set a team key in Settings" in error


def _no_env_keys(monkeypatch):
    for env_var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)


def _capture_models(monkeypatch) -> list:
    seen: list = []

    def fake_build(provider_name, api_key, model=None):
        seen.append((provider_name, model))
        raise RuntimeError("stub: stop before the LLM call")

    monkeypatch.setattr(tm_service, "build_provider", fake_build)
    monkeypatch.setattr(tm_service, "_assemble_context", lambda t, s: ("ctx", frozenset()))
    return seen


def test_preset_resolves_for_the_session_keys_provider(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "openrouter", "anthropic/claude-sonnet-4.6", "k")
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid, "cheapest"))
    assert seen == [("openrouter", "anthropic/claude-haiku-4.5:floor")]


def test_team_override_used_for_the_team_key(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    admin = auth_service.seed_admin(
        username=f"a-{uuid.uuid4().hex[:8]}",
        password="pw",
        tenant_id=tenant,
        tenant_name="t",
        role="admin",
    )
    tenant_config.save_config(
        tenant, admin, "openrouter", "team-model", "k", {"strongest": "vendor/best"}
    )
    sid = _session(tenant)
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid, "strongest"))
    assert seen == [("openrouter", "vendor/best")]


def test_no_preset_keeps_the_saved_model(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "anthropic", "claude-x", "k")
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid))
    assert seen == [("anthropic", "claude-x")]


def test_a_run_is_claimed_once(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "anthropic", "claude-x", "k")
    tm_service.trigger_generation(tenant, sid)
    asyncio.run(tm_service.run_generation(tenant, sid))
    asyncio.run(tm_service.run_generation(tenant, sid))  # no longer pending → no-op
    assert len(seen) == 1


def test_running_row_is_not_reclaimed(tenant, monkeypatch):
    _no_env_keys(monkeypatch)
    seen = _capture_models(monkeypatch)
    sid = _session(tenant)
    llm_service.save_config(tenant, sid, "anthropic", "claude-x", "k")
    tm_service.trigger_generation(tenant, sid)
    tm_service._set_status(tenant, sid, "running")
    asyncio.run(tm_service.run_generation(tenant, sid))
    assert seen == []
    assert tm_service.get_threat_model(tenant, sid)["status"] == "running"
