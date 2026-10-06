"""RLS guards for the LLM-config and threat-model tables (migrations 0026/0027).

These tables shipped with FORCE-only RLS (never ENABLEd) and a policy on a GUC the app
never sets, so isolation silently rested on the services' tenant_id filters. The
unfiltered queries below only return 0 for the other tenant if the database enforces it.
"""

import pytest
from sqlalchemy import text

from recon.db import models
from recon.db.base import tenant_session
from recon.llm import service as llm_service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_TABLES = models.LLM_TABLES + models.THREAT_MODEL_TABLES


@pytest.mark.parametrize("table", _TABLES)
def test_rls_is_enabled_and_forced(table):
    with tenant_session(sessions_service.create_tenant("rls-flags")) as session:
        enabled, forced = session.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert enabled and forced


def test_llm_config_and_threats_are_tenant_isolated_by_rls():
    tenant_a = sessions_service.create_tenant("llm-a")
    tenant_b = sessions_service.create_tenant("llm-b")
    sv = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    with tenant_session(tenant_a) as session:
        session.add(
            models.SessionLlmConfig(
                tenant_id=tenant_a, session_id=sv.id, provider="anthropic", model="m"
            )
        )
        threat_model = models.SessionThreatModel(tenant_id=tenant_a, session_id=sv.id)
        session.add(threat_model)
        session.flush()
        session.add(
            models.Threat(
                tenant_id=tenant_a,
                threat_model_id=threat_model.id,
                rank=0,
                title="t",
                owasp_category="A01",
                severity="high",
                description="d",
                affected_endpoints=[],
                test_steps=[],
                citations=[],
            )
        )
    for model in (models.SessionLlmConfig, models.SessionThreatModel, models.Threat):
        with tenant_session(tenant_a) as session:
            assert session.query(model).count() == 1
        with tenant_session(tenant_b) as session:
            assert session.query(model).count() == 0


def test_llm_service_round_trips_through_rls():
    # The services used to open plain Session(engine) with no tenant GUC; under real RLS
    # that reads nothing and the WITH CHECK rejects the insert.
    tenant_a = sessions_service.create_tenant("llm-svc-a")
    tenant_b = sessions_service.create_tenant("llm-svc-b")
    sv = sessions_service.create_session(
        tenant_a, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    saved = llm_service.save_config(tenant_a, sv.id, "anthropic", "m", "k-test")
    assert saved is not None and saved["has_key"]
    assert llm_service.get_config(tenant_a, sv.id)["model"] == "m"
    assert llm_service.load_api_key(tenant_a, sv.id) == "k-test"
    assert llm_service.get_config(tenant_b, sv.id) is None
    assert llm_service.load_api_key(tenant_b, sv.id) is None
    assert llm_service.delete_config(tenant_a, sv.id) is True
    assert llm_service.get_config(tenant_a, sv.id) is None
