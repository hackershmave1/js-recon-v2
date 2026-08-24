"""Integration: the cleartext internal-IP info-disclosure lane.

`kingfisher.scan` is faked to return NO secrets, isolating the internal-IP pass: a
private/loopback IP literal in the bundle is recorded as an `internal_ip` finding whose
value is the RAW cleartext dotted-quad (never sha256-hashed into identity, unlike a
secret), counted in the SEPARATE `internal_ips` coverage total, served un-redacted, and
NOT reveal-gated. A public IP in the same blob is skipped.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, kingfisher, queries
from recon.runs import service

pytestmark = pytest.mark.integration


def _fake_scan_no_secrets():
    def _scan(_raw: bytes, *, confidence: str | None = None, **_kw) -> kingfisher.ScanResult:
        return kingfisher.ScanResult(secrets=[], status="ok")

    return _scan


def _run_with_source(redis, tenant, session_id, src: bytes) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", src)
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))
    return view.id


def _findings(tenant, run_id):
    with tenant_session(tenant) as session:
        return list(
            session.execute(select(models.Finding).where(models.Finding.run_id == run_id)).scalars()
        )


def test_internal_ip_recorded_cleartext_counted_and_not_reveal_gated(
    redis, authorized_session, monkeypatch
):
    tenant, session_id = authorized_session
    monkeypatch.setattr(kingfisher, "scan", _fake_scan_no_secrets())
    # Two locked-range IPs (rfc1918 + loopback) plus a public one that must be SKIPPED.
    src = b'const a="http://10.0.0.1/h"; const b="127.0.0.1"; const c="8.8.8.8";'
    run_id = _run_with_source(redis, tenant, session_id, src)

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    # Coverage: the two internal IPs are counted SEPARATELY; `secrets` stays 0 (none faked),
    # so the info-disclosure lane never inflates the precision secret count.
    assert coverage.internal_ips == 2
    assert coverage.secrets == 0

    ip_rows = [f for f in _findings(tenant, run_id) if f.type == "internal_ip"]
    # The finding value is the RAW cleartext dotted-quad — NEVER provider:sha256; the public
    # 8.8.8.8 is not recorded; the category rides on attributes.
    assert {f.value for f in ip_rows} == {"10.0.0.1", "127.0.0.1"}
    assert {f.attributes["category"] for f in ip_rows} == {"rfc1918", "loopback"}

    # Read model (goes through `_finding_view`): value served cleartext, occurrence evidence
    # un-redacted, and NOT revealable — `internal_ip` is deliberately absent from the secret
    # redaction/reveal gates, so an info-disclosure IP is shown in full and never reveal-gated.
    views = {
        f.value: f
        for f in queries.list_findings(tenant, run_id).findings
        if f.type == "internal_ip"
    }
    assert set(views) == {"10.0.0.1", "127.0.0.1"}
    for value, finding in views.items():
        assert finding.value == value  # cleartext dotted-quad, not a hash
        assert ":" not in finding.value  # a secret value would carry the `provider:` prefix
        assert finding.revealable is False  # never reveal-gated
        occurrence = finding.occurrences[0]
        assert occurrence.offset_start is not None and occurrence.offset_end is not None
