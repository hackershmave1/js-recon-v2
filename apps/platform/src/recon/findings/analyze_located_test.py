"""Integration: every finding in a minified bundle stores the exact place the Sources tab shows it.

The bundle is one minified line; the Sources tab serves it beautified. Each lane locates against a
different text (tree-sitter on the beautified unit, the sink / IP regexes and Kingfisher on the raw
bytes), and each stored ``view_*`` range must slice exactly that finding's token out of the text
``recon.probe.sources`` serves — not "line 1" or a random spot.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, kingfisher, queries, reextract
from recon.findings.wrappers import WrapperRule
from recon.probe import sources
from recon.runs import service

pytestmark = pytest.mark.integration

_SECRET = "AKIAABCDEFGHIJKLMNOP"
_BUNDLE = (
    'var t=e.i(1);var s="é😀";window.addEventListener("message",function(e){t(e.data)});'
    'fetch("/api/users",{method:"POST",body:JSON.stringify({a:1})});'
    f'var ip="10.1.2.3";var k="{_SECRET}";'
    "var q=gql`query Me { me { id } }`;"
).encode()


def _fake_scan(_raw: bytes, *, confidence: str | None = None, **_kw) -> kingfisher.ScanResult:
    secret = kingfisher.RawSecret(
        rule_id="kingfisher.aws.1", rule_name="AWS", snippet=_SECRET, line=1, column_start=0
    )
    return kingfisher.ScanResult(secrets=[secret], status="ok")


def _run(redis, tenant, session_id) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", _BUNDLE)
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))
    return view.id


def _occurrences(tenant, run_id):
    with tenant_session(tenant) as session:
        return session.execute(
            select(models.Finding.type, models.FindingOccurrence)
            .join(models.FindingOccurrence.finding)
            .where(models.Finding.run_id == run_id)
        ).all()


def _highlighted(text: str, occ: models.FindingOccurrence) -> str:
    """What the browser highlights: rows split on "\\n", columns in UTF-16 code units."""
    rows = text.split("\n")
    out = []
    for line in range(occ.view_line, occ.view_end_line + 1):
        row = rows[line - 1].encode("utf-16-le")
        start = occ.view_col if line == occ.view_line else 0
        end = occ.view_end_col if line == occ.view_end_line else len(row) // 2
        out.append(row[start * 2 : end * 2].decode("utf-16-le"))
    return "".join("\n".join(out).split())  # beautify may add spaces inside a token


def test_every_lane_highlights_its_own_token_in_the_served_bundle(
    redis, authorized_session, monkeypatch
):
    tenant, session_id = authorized_session
    monkeypatch.setattr(kingfisher, "scan", _fake_scan)
    run_id = _run(redis, tenant, session_id)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    served = sources.get_source_content(tenant, run_id, "input.js")
    assert served is not None and served.formatted
    expected = {
        "endpoint": '"/api/users"',
        "postmessage_sink": 'addEventListener("message"',
        "internal_ip": "10.1.2.3",
        "secret": _SECRET,
        "graphql": "gql`queryMe{me{id}}`",
    }
    seen: dict[str, str] = {}
    for finding_type, occ in _occurrences(tenant, run_id):
        if finding_type not in expected:
            continue
        assert occ.view_line is not None, finding_type
        assert occ.view_line > 1, finding_type  # not the minified line 1
        assert occ.source_path == "input.js", finding_type  # GraphQL too: the served bundle
        seen[finding_type] = _highlighted(served.content, occ)
    assert set(seen) == set(expected)
    for finding_type, token in expected.items():
        assert seen[finding_type] == token.replace(" ", ""), finding_type


def test_a_reextract_adds_no_occurrence_and_keeps_the_locations(
    redis, authorized_session, monkeypatch
):
    tenant, session_id = authorized_session
    monkeypatch.setattr(kingfisher, "scan", _fake_scan)
    run_id = _run(redis, tenant, session_id)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    def _snapshot():
        with tenant_session(tenant) as session:
            count = session.scalar(
                select(func.count())
                .select_from(models.FindingOccurrence)
                .join(models.FindingOccurrence.finding)
                .where(models.Finding.run_id == run_id)
            )
        views = sorted(
            (t, o.view_line, o.view_col, o.view_end_line, o.view_end_col)
            for t, o in _occurrences(tenant, run_id)
        )
        return count, views

    before = _snapshot()
    reextract.reextract_run(tenant, run_id, [WrapperRule("api")])
    assert _snapshot() == before


def test_the_api_serves_the_exact_range_and_flags_old_runs_as_approximate(
    redis, authorized_session, monkeypatch
):
    tenant, session_id = authorized_session
    monkeypatch.setattr(kingfisher, "scan", _fake_scan)
    run_id = _run(redis, tenant, session_id)
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    (endpoint,) = [
        f for f in queries.list_findings(tenant, run_id).findings if f.type == "endpoint"
    ]
    (occ,) = endpoint.occurrences
    assert occ.loc is not None and occ.approximate_line is None

    # A run analyzed before exact locations: no view_* anywhere -> no loc, the old line is
    # offered only as approximate.
    with tenant_session(tenant) as session:
        session.execute(
            update(models.FindingOccurrence)
            .where(
                models.FindingOccurrence.finding_id.in_(
                    select(models.Finding.id).where(models.Finding.run_id == run_id)
                )
            )
            .values(view_line=None, view_col=None, view_end_line=None, view_end_col=None)
        )
    (endpoint,) = [
        f for f in queries.list_findings(tenant, run_id).findings if f.type == "endpoint"
    ]
    (occ,) = endpoint.occurrences
    assert occ.loc is None
    assert occ.approximate_line == occ.line
