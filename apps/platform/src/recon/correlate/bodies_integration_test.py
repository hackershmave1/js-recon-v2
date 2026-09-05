"""D45b2 — captured-body analysis against live Postgres + the REAL Kingfisher binary.

Covers the part the host-lane fakes stand in for (``bodies_test.py``): a genuine secret in a
captured payload becomes a ``SECRET`` finding with ``engine="capture"`` provenance, a matched
request body's top-level keys become ``PARAM`` findings tied to the endpoint's operation, and a
redelivery is idempotent (REQ-A3) — all written under RLS via ``tenant_session``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from recon.correlate import bodies
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import kingfisher
from recon.findings.normalize import finding_hash, normalize_param_value

pytestmark = pytest.mark.integration

# A known Kingfisher-detectable Stripe key, built from split literals so no secret-shaped token
# is committed; the engine reassembles + detects it at runtime (same shape as the analyze tests).
_TOKEN = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
_ENDPOINT_VALUE = "POST /charge"
_ENDPOINT_PATH = "app/pay.js"
_URL = "https://api.acme.io/charge"


def _make_run(tenant: str, session_id: str) -> str:
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="correlating")
        session.add(run)
        session.flush()
        return str(run.id)


def _findings_of_type(tenant: str, run_id: str, ftype: str) -> dict[str, list[tuple]]:
    """``{value: [(engine, host, raw_url, source_path), ...]}`` for the run's findings of a type."""
    with tenant_session(tenant) as session:
        rows = session.query(models.Finding).filter_by(run_id=run_id, type=ftype).all()
        return {
            row.value: [(o.engine, o.host, o.raw_url, o.source_path) for o in row.occurrences]
            for row in rows
        }


def _run(tenant: str, run_id: str, observed: list[dict]) -> None:
    """Invoke the body pass exactly as ``correlate_run`` does — the endpoint the request body's
    URL matched is supplied via ``resolved``/``by_hash`` (an in-memory FindingView stand-in)."""
    endpoint_hash = finding_hash("endpoint", _ENDPOINT_VALUE)
    resolved = {endpoint_hash: _URL}
    by_hash = {
        endpoint_hash: SimpleNamespace(
            finding_hash=endpoint_hash, type="endpoint", value=_ENDPOINT_VALUE, path=_ENDPOINT_PATH
        )
    }
    with patch("recon.correlate.bodies.publish"):
        bodies.analyze_bodies(
            MagicMock(),
            tenant_id=tenant,
            run_id=run_id,
            observed=observed,
            resolved=resolved,
            by_hash=by_hash,
        )


def _require_kingfisher(engines_required: bool) -> None:
    if kingfisher.scan(_TOKEN.encode("utf-8")).status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")


def test_body_secret_and_params_get_capture_provenance(authorized_session, engines_required):
    tenant, session_id = authorized_session
    _require_kingfisher(engines_required)
    run_id = _make_run(tenant, session_id)
    # A JSON request body carrying a real secret + two top-level keys; scanned AND param-mined.
    req_body = json.dumps({"amount": 10, "apiKey": _TOKEN})
    _run(tenant, run_id, [{"method": "POST", "url": _URL, "reqBody": req_body}])

    # SECRET: recorded with engine="capture" and the synthetic capture-request source_path.
    secrets = _findings_of_type(tenant, run_id, "secret")
    stripe = {v: occs for v, occs in secrets.items() if v.startswith("stripe:")}
    assert stripe, f"expected a stripe secret from the body, got {list(secrets)}"
    occs = next(iter(stripe.values()))
    assert (
        "capture",
        "api.acme.io",
        _URL,
        "capture-request://api.acme.io/charge",
    ) in occs

    # PARAM: top-level keys tied to the matched endpoint's operation, capture provenance.
    params = _findings_of_type(tenant, run_id, "param")
    assert normalize_param_value(_ENDPOINT_VALUE, "body", "amount") in params
    assert normalize_param_value(_ENDPOINT_VALUE, "body", "apiKey") in params
    amount_occs = params[normalize_param_value(_ENDPOINT_VALUE, "body", "amount")]
    assert amount_occs == [("capture", "api.acme.io", _URL, "capture-request://api.acme.io/charge")]


def test_body_analysis_rerun_is_idempotent(authorized_session, engines_required):
    tenant, session_id = authorized_session
    _require_kingfisher(engines_required)
    run_id = _make_run(tenant, session_id)
    observed = [{"method": "POST", "url": _URL, "reqBody": json.dumps({"apiKey": _TOKEN})}]

    _run(tenant, run_id, observed)
    _run(tenant, run_id, observed)  # at-least-once redelivery must not double-write

    secrets = _findings_of_type(tenant, run_id, "secret")
    stripe_occs = [occs for v, occs in secrets.items() if v.startswith("stripe:")]
    assert stripe_occs and all(len(occs) == 1 for occs in stripe_occs)
    params = _findings_of_type(tenant, run_id, "param")
    assert params[normalize_param_value(_ENDPOINT_VALUE, "body", "apiKey")] == [
        ("capture", "api.acme.io", _URL, "capture-request://api.acme.io/charge")
    ]
