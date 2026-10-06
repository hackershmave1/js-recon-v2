"""run_generation must fail loudly, never stay 'running', when the stored key can't be decrypted."""

import asyncio

import pytest

from recon.llm import service as llm_service
from recon.llm.crypto import KeyDecryptError
from recon.sessions import service as sessions_service
from recon.threat_model import service as tm_service

pytestmark = pytest.mark.integration


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
    asyncio.run(tm_service.run_generation(tenant, sid))
    state = tm_service.get_threat_model(tenant, sid)
    assert state["status"] == "failed"
    assert state["error"] == "stored LLM key could not be decrypted; re-save it"


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
