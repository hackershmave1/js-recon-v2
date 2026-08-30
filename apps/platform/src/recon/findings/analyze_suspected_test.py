"""D33-B: the opt-in low-confidence "suspected secret" tier.

`kingfisher.scan` is faked to return mixed-confidence sightings, so the partition
(low → SECRET_SUSPECTED, medium/high → SECRET), the SEPARATE coverage counts, the
opt-in threading (a NULL flag scans at medium), and the reuse of the SECRET
reveal/redaction machinery are all exercised without the real binary.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, kingfisher, normalize, queries
from recon.probe import reveal
from recon.runs import service

pytestmark = pytest.mark.integration

# Non-secret-shaped markers: the scan is FAKED, so these only need to be locatable in the
# source blob (locate_snippet) and are hashed into the finding value regardless of shape —
# keeping them free of any real secret prefix so the gitleaks lane stays clean.
_MED = "medium-lane-marker-000111"
_LOW = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
_LOW_RULE = "custom.config.guid_assignment_low"


def _sec(
    snippet: str, confidence: str, rule_id: str = "kingfisher.stripe.2"
) -> kingfisher.RawSecret:
    return kingfisher.RawSecret(
        rule_id=rule_id, rule_name="r", snippet=snippet, confidence=confidence
    )


def _fake_scan(*secrets: kingfisher.RawSecret):
    def _scan(_raw: bytes, *, confidence: str | None = None, **_kw) -> kingfisher.ScanResult:
        _scan.confidence = confidence  # type: ignore[attr-defined]
        return kingfisher.ScanResult(secrets=list(secrets), status="ok")

    return _scan


def _run_with_source(redis, tenant, session_id, src: bytes, *, opted_in: bool) -> str:
    view = service.create_run(
        redis, tenant_id=tenant, session_id=session_id, scan_suspected_secrets=opted_in or None
    )
    key = storage.put_blob(tenant, view.id, "input", src)
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))
    return view.id


def _findings(tenant, run_id):
    with tenant_session(tenant) as session:
        return list(
            session.execute(select(models.Finding).where(models.Finding.run_id == run_id)).scalars()
        )


def test_opted_in_run_partitions_low_confidence_as_suspected(
    redis, authorized_session, monkeypatch
):
    tenant, session_id = authorized_session
    scan = _fake_scan(_sec(_MED, "medium"), _sec(_LOW, "low", _LOW_RULE))
    monkeypatch.setattr(kingfisher, "scan", scan)
    src = f'const m="{_MED}"; const c="{_LOW}";'.encode()
    run_id = _run_with_source(redis, tenant, session_id, src, opted_in=True)

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    assert scan.confidence == "low"  # the opt-in threaded through to the scan
    by_type = {f.type for f in _findings(tenant, run_id)}
    assert "secret" in by_type and "secret_suspected" in by_type
    # The medium hit is a precision SECRET; the low hit is the SUSPECTED lane — counted
    # SEPARATELY so the precision `secrets` count is never inflated by the recall lane.
    assert coverage.secrets == 1
    assert coverage.secrets_suspected == 1


def test_default_run_scans_medium_and_records_no_suspected(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    scan = _fake_scan(_sec(_MED, "medium"))
    monkeypatch.setattr(kingfisher, "scan", scan)
    run_id = _run_with_source(
        redis, tenant, session_id, f'const m="{_MED}";'.encode(), opted_in=False
    )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    assert scan.confidence is None  # not opted in → default medium scan (unchanged)
    assert {f.type for f in _findings(tenant, run_id)} == {"secret"}
    assert coverage.secrets == 1 and coverage.secrets_suspected == 0


def test_suspected_secret_is_hashed_revealable_and_reveals(redis, authorized_session, monkeypatch):
    # The suspected tier reuses the SECRET machinery: value is provider:sha256 (never the
    # raw token — REQ-S2), the read model marks it revealable, and the audited reveal
    # round-trips to the plaintext.
    tenant, session_id = authorized_session
    monkeypatch.setattr(kingfisher, "scan", _fake_scan(_sec(_LOW, "low", _LOW_RULE)))
    run_id = _run_with_source(
        redis, tenant, session_id, f'const c="{_LOW}";'.encode(), opted_in=True
    )

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    suspected = [
        f for f in queries.list_findings(tenant, run_id).findings if f.type == "secret_suspected"
    ]
    assert len(suspected) == 1
    finding = suspected[0]
    assert finding.value.startswith("config:") and _LOW not in finding.value  # hashed, not raw
    assert finding.revealable is True
    outcome = reveal.reveal_secret(tenant, run_id, finding.finding_hash)
    assert outcome is not None and outcome.revealed is True
    assert normalize.strip_secret_delimiters(outcome.value) == _LOW


def test_recovered_source_low_confidence_hit_is_suspected(redis, authorized_session, monkeypatch):
    # The RECOVERED-source partition path (D32-B1 scan + D33-B partition): a low-confidence hit
    # in a source-map-recovered unit is recorded as SECRET_SUSPECTED at its recovered path and
    # counted in secrets_suspected, not secrets. Fakes iter_recovered_files (→ a recovered unit,
    # D37-L2 slice 3) and scan_dir (→ a low hit on it); the bundle scan is empty.
    from recon.findings import sourcemapper

    tenant, session_id = authorized_session
    recovered = f'// prod\nconst c = "{_LOW}";\n'.encode()
    monkeypatch.setattr(kingfisher, "scan", _fake_scan())  # bundle: no secret

    def fake_iter(_map_path, **_kwargs):
        yield "app/config.js", recovered

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", fake_iter)

    def fake_scan_dir(_tree_root, *, confidence=None, **_kw):
        assert confidence == "low"  # opted-in confidence threads to the recovered scan too
        return {"app/config.js": [_sec(_LOW, "low", _LOW_RULE)]}, "ok"

    monkeypatch.setattr(kingfisher, "scan_dir", fake_scan_dir)

    view = service.create_run(
        redis, tenant_id=tenant, session_id=session_id, scan_suspected_secrets=True
    )
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/api/x");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )

    coverage = analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    suspected = [f for f in _findings(tenant, view.id) if f.type == "secret_suspected"]
    assert len(suspected) == 1 and suspected[0].path.endswith("config.js")  # recovered path
    assert coverage.secrets == 0 and coverage.secrets_suspected == 1
