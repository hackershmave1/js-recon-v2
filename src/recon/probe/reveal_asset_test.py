"""Slice Y: a crawl-run secret reveals by slicing its own asset blob."""

from __future__ import annotations

import pytest

from recon import storage
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import normalize
from recon.findings.store import Occurrence, record_finding
from recon.probe import reveal
from recon.runs import assets, service

pytestmark = pytest.mark.integration


def test_reveal_slices_the_occurrences_asset_blob(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    run_id = view.id
    token = "AKIA" + "I" * 16  # format-broken placeholder shape
    blob = f'const k = "{token}";'.encode()
    key = storage.put_blob(tenant, run_id, "input", blob)
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=["https://acme.io/a.js"])
    asset = assets.list_for_run(tenant, run_id)[0]
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, asset.id, key)
    start = blob.index(token.encode()); end = start + len(token)
    value = normalize.normalize_secret_value(token, "aws-access-key-id")
    with tenant_session(tenant) as s:
        record_finding(s, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
                       value=value, path="input.js",
                       occurrence=Occurrence(
                           run_asset_id=asset.id, asset_url=asset.url,
                           source_path="input.js", offset_start=start, offset_end=end,
                       ),
                       attributes={"rule": "aws-access-key-id"})
        fh = normalize.finding_hash(FindingType.SECRET.value, value, "input.js")

    outcome = reveal.reveal_secret(tenant, run_id, fh)
    assert outcome is not None and outcome.revealed and outcome.value == token
