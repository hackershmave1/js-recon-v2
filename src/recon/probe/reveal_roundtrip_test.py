import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, kingfisher, normalize, queries
from recon.probe import reveal

pytestmark = pytest.mark.integration


def test_reveal_roundtrips_real_kingfisher_offsets(redis, authorized_session, engines_required):
    tenant, session_id = authorized_session
    # Split literals so no secret-shaped token is committed; kingfisher reassembles.
    token = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
    js = f'const apiKey = "{token}";\nfetch("/api/ping");\n'
    if kingfisher.scan(js.encode("utf-8")).status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")

    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    input_ref = storage.put_blob(tenant, run_id, "input", js.encode("utf-8"))
    with tenant_session(tenant) as session:
        session.get(models.Run, run_id).input_ref = input_ref

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    result = queries.list_findings(tenant, run_id)
    secret = next(f for f in result.findings if f.type == "secret")
    assert secret.revealable is True

    # The round-trip: real Kingfisher line/column -> byte_offset -> blob slice ->
    # provider:sha256 must match. A 409 here means byte_offset's column convention
    # is wrong for the real engine (see contingency below), NOT that reveal is broken.
    outcome = reveal.reveal_secret(tenant, run_id, secret.finding_hash)
    assert outcome is not None and outcome.revealed is True
    assert token in outcome.value
    assert normalize.strip_secret_delimiters(outcome.value) == token
