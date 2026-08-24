"""D32-B1: a secret living only in a source map's recovered ``sourcesContent`` is
revealed by re-deriving that recovered byte space (not the raw bundle).

``recover_sources`` is faked (deterministic) so analyze and reveal reproduce identical
bytes without a Go binary; Kingfisher is real — it detects the planted token and locates
its offset. The fail-closed test then makes the map drift under the recorded offset and
asserts reveal REFUSES rather than returning wrong plaintext (the security invariant).
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, kingfisher, normalize, queries, sourcemapper
from recon.probe import reveal
from recon.runs import service

pytestmark = pytest.mark.integration

# Split literals so no secret-shaped token is committed; Kingfisher reassembles it. The
# token sits in a COMMENT plus a const — content a minifier strips from the shipped
# bundle, so it exists ONLY in the recovered original (the exact D32-B1 gap).
_TOKEN = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
_RECOVERED = (
    f'// prod config, stripped from the minified bundle\nconst KEY = "{_TOKEN}";\n'
    'export const pay = () => fetch("/api/pay");\n'
).encode()


def _skip_if_no_kingfisher(engines_required) -> None:
    if kingfisher.scan(_RECOVERED).status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")


def _fake_recover(content: bytes):
    def _recover(_map_bytes, **_kwargs):
        return sourcemapper.RecoveredSources(
            files=[sourcemapper.RecoveredFile("app/src/config.js", content)],
            status="ok",
            origin="uploaded",
        )

    return _recover


def _seed_run_with_recovered_secret(redis, tenant, session_id) -> str:
    """A legacy run whose minified bundle carries NO secret and whose (faked) source map
    recovers ``_RECOVERED`` — so the only secret is recovered-only."""
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/api/pay");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)
    return view.id


def _the_secret(tenant, run_id):
    secrets = [f for f in queries.list_findings(tenant, run_id).findings if f.type == "secret"]
    assert len(secrets) == 1, "expected exactly the recovered-only secret"
    return secrets[0]


def test_reveal_roundtrips_a_recovered_source_secret(
    redis, authorized_session, monkeypatch, engines_required
):
    _skip_if_no_kingfisher(engines_required)
    tenant, session_id = authorized_session
    monkeypatch.setattr(sourcemapper, "recover_sources", _fake_recover(_RECOVERED))

    run_id = _seed_run_with_recovered_secret(redis, tenant, session_id)
    secret = _the_secret(tenant, run_id)
    assert secret.revealable is True  # the read-gate promises it

    outcome = reveal.reveal_secret(tenant, run_id, secret.finding_hash)
    assert outcome is not None and outcome.revealed is True
    # Re-derived from the map (not the bundle), the recorded offset round-trips to the token.
    assert normalize.strip_secret_delimiters(outcome.value) == _TOKEN


def test_reveal_recovered_secret_fails_closed_when_the_map_drifts(
    redis, authorized_session, monkeypatch, engines_required
):
    # THE security invariant: if the re-derived recovered bytes no longer hash to the
    # finding identity (the map changed under the same offsets), reveal REFUSES — it must
    # never return wrong plaintext. A prefix line shifts every byte so the recorded span
    # no longer bounds the token.
    _skip_if_no_kingfisher(engines_required)
    tenant, session_id = authorized_session
    monkeypatch.setattr(sourcemapper, "recover_sources", _fake_recover(_RECOVERED))
    run_id = _seed_run_with_recovered_secret(redis, tenant, session_id)
    secret = _the_secret(tenant, run_id)

    drifted = b"// a banner line added after analyze shifts every offset\n" + _RECOVERED
    monkeypatch.setattr(sourcemapper, "recover_sources", _fake_recover(drifted))

    outcome = reveal.reveal_secret(tenant, run_id, secret.finding_hash)
    assert outcome is not None and outcome.revealed is False
    assert outcome.denial == "integrity"  # fail-closed, not wrong bytes
    assert outcome.value is None
