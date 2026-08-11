import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from recon.correlate import stage


def _finding(finding_hash, value, occurrences=(), path="input.js"):
    return SimpleNamespace(
        finding_hash=finding_hash,
        type="endpoint",
        value=value,
        path=path,
        occurrences=list(occurrences),
    )


def _occ(host=None, raw_url=None, engine="vespasian"):
    return SimpleNamespace(host=host, raw_url=raw_url, engine=engine)


def _run_correlate(*, observed, findings, requests_ref="blob/req"):
    payload = {"requests_ref": requests_ref} if requests_ref else {}
    recorded: list[dict] = []
    events: list[dict] = []

    def _record(_session, **kw):
        recorded.append(kw)
        return SimpleNamespace(occurrence_created=True)

    with (
        patch(
            "recon.correlate.stage.discover_queries.latest_assets_event",
            return_value=payload or None,
        ),
        patch("recon.correlate.stage.storage.get_blob", return_value=json.dumps(observed).encode()),
        patch(
            "recon.correlate.stage.findings_queries.list_findings",
            return_value=SimpleNamespace(findings=findings),
        ),
        patch("recon.correlate.stage.run_queries.raise_if_control_requested"),
        patch("recon.correlate.stage.store.record_finding", side_effect=_record),
        patch("recon.correlate.stage.tenant_session"),
        patch(
            "recon.correlate.stage.record_event",
            side_effect=lambda *a, **k: events.append(k) or MagicMock(),
        ),
        patch("recon.correlate.stage.publish"),
    ):
        stage.correlate_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    return recorded, events


def test_capture_run_resolves_hostless_endpoints():
    # The two motivating shapes: a leading-var host template and a bare-relative path,
    # each resolved to the real observed URL and written as a capture occurrence.
    observed = [
        {"method": "GET", "url": "https://api.acme.io/get-job-types"},
        {"method": "POST", "url": "https://api.acme.io/getJobId"},
    ]
    findings = [
        _finding("h1", "GET /${baseDomainName}/get-job-types", [_occ()]),
        _finding("h2", "POST /getJobId", [_occ()]),
    ]
    recorded, events = _run_correlate(observed=observed, findings=findings)

    by_value = {kw["value"]: kw for kw in recorded}
    assert set(by_value) == {"GET /${baseDomainName}/get-job-types", "POST /getJobId"}
    occ = by_value["GET /${baseDomainName}/get-job-types"]["occurrence"]
    assert (occ.host, occ.raw_url, occ.engine) == (
        "api.acme.io",
        "https://api.acme.io/get-job-types",
        "capture",
    )
    assert events[0]["event_type"] == "correlate.resolved"
    assert events[0]["payload"] == {"observed": 2, "resolved": 2, "written": 2}


def test_non_capture_run_writes_nothing_and_emits_no_event():
    # No requests_ref on the discover.assets event (a static crawl) -> clean no-op.
    recorded, events = _run_correlate(
        observed=[], findings=[_finding("h", "GET /x", [_occ()])], requests_ref=None
    )
    assert recorded == []
    assert events == []


def test_query_bearing_endpoint_value_still_correlates():
    # A stored endpoint value carries a ?query suffix (normalize_endpoint appends it); it
    # must be stripped before matching so a query-bearing endpoint still resolves against
    # the (query-less) observed URL — else search/list/filter endpoints silently miss.
    observed = [{"method": "GET", "url": "https://api.acme.io/search"}]
    findings = [_finding("h", "GET /search?q", [_occ()])]
    recorded, _events = _run_correlate(observed=observed, findings=findings)
    assert len(recorded) == 1
    assert recorded[0]["occurrence"].raw_url == "https://api.acme.io/search"


def test_already_absolute_endpoint_is_left_alone():
    # A finding already observed with a host is absolute (resolved) — not re-correlated,
    # which is also what makes a re-run idempotent. The event still records the counts.
    observed = [{"method": "GET", "url": "https://api.acme.io/foo"}]
    findings = [
        _finding(
            "h",
            "GET /foo",
            [_occ(host="static.example.com", raw_url="https://static.example.com/foo")],
        )
    ]
    recorded, events = _run_correlate(observed=observed, findings=findings)

    assert recorded == []
    assert events[0]["payload"] == {"observed": 1, "resolved": 0, "written": 0}
