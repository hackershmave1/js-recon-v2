import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze
from recon.findings.kingfisher import RawSecret
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def test_record_secret_stores_offsets_but_not_plaintext():
    tenant = sessions_service.create_tenant("wsec-1")
    view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    token = "sk_" + "live_" + "AKIAEXAMPLE1234567890"
    source = f'const k = "{token}";\n'
    secret = RawSecret(
        rule_id="stripe", rule_name="Stripe", snippet=token,
        line=1, column_start=source.index(token),
    )
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        analyze._record_secret(session, tenant, run_id, "input.js", source, secret)

    with tenant_session(tenant) as session:
        occurrence = session.query(models.FindingOccurrence).one()
        assert occurrence.evidence is None  # model A: no plaintext at rest
        assert occurrence.offset_start is not None and occurrence.offset_end is not None
        # the stored offsets bound the token in the source's byte space
        sliced = source.encode("utf-8")[occurrence.offset_start:occurrence.offset_end]
        assert sliced.decode("utf-8") == token
