import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, kingfisher, normalize, queries
from recon.probe import reveal

pytestmark = pytest.mark.integration


def test_reveal_roundtrips_real_kingfisher_offsets(redis, authorized_session, engines_required):
    tenant, session_id = authorized_session
    # Split literals so no secret-shaped token is committed; kingfisher reassembles
    # the CONTIGUOUS tokens at runtime (the "+" splits live only in this file).
    #
    # Two providers on DIFFERENT lines, with the AWS access-key id ABOVE its secret
    # access key. AWS-style rules report their match region near the id — a
    # different line than the extracted secret snippet — so deriving the byte offset
    # from the engine's line/column sliced the wrong bytes and the reveal
    # fail-closed (409). This guards the content-locating fix
    # (recon.findings.kingfisher.locate_snippet) for AWS *and* Stripe, not just a
    # single single-line token.
    aws_id = "AKIA" + "2E4XZ7K9QW3RT8YV"
    aws_secret = "wJalr" + "XUtnFEMIK7MDENGbPxRfiCYzEXKEYFAKE01"
    stripe_key = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dcTESTONLY"
    js = (
        f'const AWS_ACCESS_KEY_ID = "{aws_id}";\n'
        f'const AWS_SECRET_ACCESS_KEY = "{aws_secret}";\n'
        f'const STRIPE = "{stripe_key}";\n'
        'fetch("/api/ping");\n'
    )
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
    secrets = [f for f in result.findings if f.type == "secret"]
    assert secrets, "expected real Kingfisher to report secret findings"

    # EVERY secret must reveal (no 409), and the AWS secret + Stripe key must both
    # round-trip to their exact plaintext. Before the fix, the AWS secret 409'd
    # because its offset was derived from the (different-line) match region.
    revealed: list[str] = []
    for secret in secrets:
        assert secret.revealable is True
        outcome = reveal.reveal_secret(tenant, run_id, secret.finding_hash)
        assert outcome is not None and outcome.revealed is True, (
            f"reveal fail-closed for {secret.value} (offset did not round-trip)"
        )
        revealed.append(normalize.strip_secret_delimiters(outcome.value))
    assert aws_secret in revealed
    assert stripe_key in revealed
