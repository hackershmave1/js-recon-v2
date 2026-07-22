from recon.findings.queries import FindingView, OccurrenceView
from recon.probe import reconstruct


def _occ(host=None, raw_url=None):
    return OccurrenceView(
        host=host, raw_url=raw_url, source_path=None, line=1, col=1,
        offset_start=0, offset_end=1, evidence=None, engine="vespasian",
        confidence=None, verified=None,
    )


def _endpoint(value, *, host=None, raw_url=None, finding_hash="e1"):
    method = value.split(" ", 1)[0]
    return FindingView(
        finding_hash=finding_hash, type="endpoint", value=value, path="input.js",
        severity=None, attributes={"method": method, "kind": "fetch"},
        first_stage="analyzing", occurrences=[_occ(host=host, raw_url=raw_url)],
    )


def _param(value, location, name, finding_hash="p1"):
    return FindingView(
        finding_hash=finding_hash, type="param", value=value, path="input.js",
        severity=None, attributes={"location": location, "name": name},
        first_stage="analyzing", occurrences=[],
    )


def test_build_groups_endpoint_with_its_params_by_operation():
    findings = [
        _endpoint("POST /api/users/{id}", host="api.acme.io", raw_url="/api/users/42"),
        _param("POST /api/users/{id} body:name", "body", "name"),
        _param("POST /api/users/{id} query:trace", "query", "trace"),
    ]
    reqs = reconstruct.build_requests(findings)
    assert len(reqs) == 1
    req = reqs[0]
    assert req.method == "POST"
    assert req.path == "/api/users/{id}"
    assert req.hosts == ("api.acme.io",)
    assert req.body_params == ("name",)
    assert [q.name for q in req.query_params] == ["trace"]
    assert req.content_type == "application/json"
    assert req.example_url == "/api/users/42"
    assert req.probeable is True
    assert req.endpoint_hash == "e1"


def test_build_seeds_query_example_from_raw_url():
    findings = [
        _endpoint("GET /search", host="api.acme.io", raw_url="/search?q=shoes"),
        _param("GET /search query:q", "query", "q"),
    ]
    (req,) = reconstruct.build_requests(findings)
    assert req.query_params[0].name == "q"
    assert req.query_params[0].example == "shoes"


def test_build_unions_hosts_across_occurrences():
    findings = [
        _endpoint("GET /a", host="one.acme.io", raw_url="https://one.acme.io/a", finding_hash="e1"),
        _endpoint("GET /a", host="two.acme.io", raw_url="https://two.acme.io/a", finding_hash="e2"),
    ]
    (req,) = reconstruct.build_requests(findings)
    assert req.hosts == ("one.acme.io", "two.acme.io")
    # endpoint_hash is deterministic: the minimum of the two finding_hashes
    assert req.endpoint_hash == "e1"


def test_build_marks_websocket_not_probeable():
    findings = [_endpoint("WSS /socket", host="api.acme.io", raw_url="wss://api.acme.io/socket")]
    (req,) = reconstruct.build_requests(findings)
    assert req.probeable is False


def test_build_endpoint_without_params_has_no_body():
    findings = [_endpoint("GET /ping", host="api.acme.io", raw_url="/ping")]
    (req,) = reconstruct.build_requests(findings)
    assert req.body_params == ()
    assert req.content_type is None


def test_build_ignores_params_without_a_matching_endpoint():
    """REQ-C2 honesty: params without an endpoint are silently dropped (not invented)."""
    findings = [
        _param("POST /api/users/{id} body:name", "body", "name"),
        _param("POST /api/users/{id} query:trace", "query", "trace"),
    ]
    # No endpoint finding for "POST /api/users/{id}", so zero requests are built
    reqs = reconstruct.build_requests(findings)
    assert reqs == []
