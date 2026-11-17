"""D64: _assemble_context reads FindingOccurrence -> RunAsset -> the served blob for code windows."""

from __future__ import annotations

import pytest

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import FindingType, RunState
from recon.findings import normalize
from recon.findings.located import ViewLoc
from recon.findings.store import Occurrence, record_finding
from recon.probe import formatted_text
from recon.runs import assets
from recon.runs import service as runs_service
from recon.threat_model.context import _assemble_context

pytestmark = pytest.mark.integration

TOKEN = "AKIA" + "W" * 16  # placeholder shape, never a real key
CODE = "\n".join(
    [
        "const client = makeClient();",
        f'const awsKey = "{TOKEN}";',
        "export function removeUser(id) {",
        '  return fetch("/api/users/" + id, { method: "DELETE" });',
        "}",
    ]
)


def _run_with_asset(redis, tenant, session_id, *, store_blob=True):
    run_id = runs_service.create_run(
        redis, tenant_id=tenant, session_id=session_id, target="acme.io"
    ).id
    if store_blob:
        key = storage.put_blob(tenant, run_id, "input", CODE.encode())
        formatted_text.store_served(key, CODE, formatted=True)
    else:
        key = f"{tenant}/{run_id}/input/{'0' * 64}"  # a blob that was never written
    with tenant_session(tenant) as s:
        assets.seed_pending(s, tenant_id=tenant, run_id=run_id, urls=["https://acme.io/app.js"])
    asset = assets.list_for_run(tenant, run_id)[0]
    with tenant_session(tenant) as s:
        assets.set_fetch_ok(s, asset.id, key)
        s.get(Run, run_id).state = RunState.DONE.value
    return run_id, asset


def _record(tenant, run_id, asset, finding_type, value, view, attributes):
    with tenant_session(tenant) as s:
        record_finding(
            s,
            tenant_id=tenant,
            run_id=run_id,
            finding_type=finding_type,
            value=value,
            path="input.js",
            occurrence=Occurrence(
                run_asset_id=asset.id,
                asset_url=asset.url,
                source_path="input.js",
                view=view,
            ),
            attributes=attributes,
        )


def _endpoint(tenant, run_id, asset):
    _record(
        tenant,
        run_id,
        asset,
        FindingType.ENDPOINT,
        "/api/users/{id}",
        ViewLoc(4, 9, 4, 24),
        {"method": "DELETE"},
    )


def _secret(tenant, run_id, asset, view):
    value = normalize.normalize_secret_value(TOKEN, "aws-access-key-id")
    _record(tenant, run_id, asset, FindingType.SECRET, value, view, {"rule": "aws-access-key-id"})


def test_context_quotes_the_call_site_and_masks_the_secret(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id, asset = _run_with_asset(redis, tenant, session_id)
    _endpoint(tenant, run_id, asset)
    col = CODE.split("\n")[1].index(TOKEN)
    _secret(tenant, run_id, asset, ViewLoc(2, col, 2, col + len(TOKEN)))

    markdown = _assemble_context(tenant, session_id).markdown

    assert "## Code Context (1 call sites in 1 windows" in markdown
    assert "### https://acme.io/app.js (lines 1-5)" in markdown
    assert "- https://acme.io/app.js:4 — [DELETE] /api/users/{id}" in markdown
    assert 'fetch("/api/users/" + id, { method: "DELETE" })' in markdown
    assert TOKEN not in markdown
    assert 'const awsKey = "[REDACTED SECRET]";' in markdown


def test_an_unlocated_secret_keeps_its_file_out_of_the_prompt(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id, asset = _run_with_asset(redis, tenant, session_id)
    _endpoint(tenant, run_id, asset)
    _secret(tenant, run_id, asset, None)  # analyzed, but no exact location

    markdown = _assemble_context(tenant, session_id).markdown

    assert TOKEN not in markdown
    assert "## Code Context" not in markdown
    assert "[DELETE] /api/users/{id}" in markdown  # the endpoint list is unchanged


def test_a_missing_blob_still_builds_the_context(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id, asset = _run_with_asset(redis, tenant, session_id, store_blob=False)
    _endpoint(tenant, run_id, asset)

    context = _assemble_context(tenant, session_id)

    assert context.run_id == run_id
    assert "## API Endpoints (1 total)" in context.markdown
    assert "## Code Context" not in context.markdown
