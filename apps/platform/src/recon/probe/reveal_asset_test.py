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
    start = blob.index(token.encode())
    end = start + len(token)
    value = normalize.normalize_secret_value(token, "aws-access-key-id")
    with tenant_session(tenant) as s:
        record_finding(
            s,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.SECRET,
            value=value,
            path="input.js",
            occurrence=Occurrence(
                run_asset_id=asset.id,
                asset_url=asset.url,
                source_path="input.js",
                offset_start=start,
                offset_end=end,
            ),
            attributes={"rule": "aws-access-key-id"},
        )
        fh = normalize.finding_hash(FindingType.SECRET.value, value, "input.js")

    outcome = reveal.reveal_secret(tenant, run_id, fh)
    assert outcome is not None and outcome.revealed and outcome.value == token


def test_reveal_asset_routed_integrity_mismatch_is_409(redis, authorized_session):
    # The fail-closed drift re-check must still fire through the asset-routed
    # branch, not just the legacy run.input_ref one.
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    run_id = view.id
    token = "AKIA" + "I" * 16  # the bytes actually written into the asset blob
    blob = f'const k = "{token}";'.encode()
    key = storage.put_blob(tenant, run_id, "input", blob)
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=["https://acme.io/a.js"])
    asset = assets.list_for_run(tenant, run_id)[0]
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, asset.id, key)
    start = blob.index(token.encode())
    end = start + len(token)
    # The finding's identity is seeded from a DIFFERENT token than the blob bytes,
    # so slicing the correctly-routed asset blob still won't hash-match.
    wrong_token = "AKIA" + "J" * 16
    wrong_value = normalize.normalize_secret_value(wrong_token, "aws-access-key-id")
    with tenant_session(tenant) as s:
        record_finding(
            s,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.SECRET,
            value=wrong_value,
            path="input.js",
            occurrence=Occurrence(
                run_asset_id=asset.id,
                asset_url=asset.url,
                source_path="input.js",
                offset_start=start,
                offset_end=end,
            ),
            attributes={"rule": "aws-access-key-id"},
        )
        fh = normalize.finding_hash(FindingType.SECRET.value, wrong_value, "input.js")

    outcome = reveal.reveal_secret(tenant, run_id, fh)
    assert outcome is not None
    assert outcome.revealed is False
    assert outcome.denial == "integrity"
    assert reveal.DENIAL_STATUS["integrity"] == 409


def test_reveal_skips_a_pending_sibling_asset_to_reveal_from_the_fetched_one(
    redis, authorized_session
):
    # queries.revealable is True whenever ANY offset-bearing occurrence's blob
    # resolves, but reveal._reveal_candidates' deterministic sort order need not
    # put that occurrence first. Here the occurrence on the still-PENDING asset
    # sorts first (source_path "a-..." < "b-..."); reveal must skip it and use
    # the fetched sibling instead of denying source_gone.
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    run_id = view.id
    token = "AKIA" + "I" * 16
    blob = f'const k = "{token}";'.encode()
    start = blob.index(token.encode())
    end = start + len(token)
    value = normalize.normalize_secret_value(token, "aws-access-key-id")

    with tenant_session(tenant) as s:
        assets.seed_pending(
            s,
            tenant_id=tenant,
            run_id=run_id,
            urls=["https://acme.io/a-pending.js", "https://acme.io/b-fetched.js"],
        )
    pending_asset, fetched_asset = assets.list_for_run(tenant, run_id)  # ordered by url
    key = storage.put_blob(tenant, run_id, "input", blob)
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, fetched_asset.id, key)
        # pending_asset keeps input_ref=None: its fetch never completed.

    with tenant_session(tenant) as s:
        record_finding(
            s,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.SECRET,
            value=value,
            path="input.js",
            occurrence=Occurrence(
                run_asset_id=pending_asset.id,
                asset_url=pending_asset.url,
                source_path="a-pending.js",
                offset_start=start,
                offset_end=end,
            ),
            attributes={"rule": "aws-access-key-id"},
        )
        record_finding(
            s,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=FindingType.SECRET,
            value=value,
            path="input.js",
            occurrence=Occurrence(
                run_asset_id=fetched_asset.id,
                asset_url=fetched_asset.url,
                source_path="b-fetched.js",
                offset_start=start,
                offset_end=end,
            ),
            attributes={"rule": "aws-access-key-id"},
        )
        fh = normalize.finding_hash(FindingType.SECRET.value, value, "input.js")

    outcome = reveal.reveal_secret(tenant, run_id, fh)
    assert outcome is not None and outcome.revealed and outcome.value == token
