from recon.findings.queries import FindingView, OccurrenceView
from recon.probe import reconstruct


def _occ(host=None, raw_url=None):
    return OccurrenceView(
        host=host,
        raw_url=raw_url,
        source_path=None,
        line=1,
        col=1,
        offset_start=0,
        offset_end=1,
        evidence=None,
        engine="vespasian",
        confidence=None,
        verified=None,
    )


def _endpoint(value, *, host=None, raw_url=None, finding_hash="e1"):
    method = value.split(" ", 1)[0]
    return FindingView(
        finding_hash=finding_hash,
        type="endpoint",
        value=value,
        path="input.js",
        severity=None,
        attributes={"method": method, "kind": "fetch"},
        first_stage="analyzing",
        occurrences=[_occ(host=host, raw_url=raw_url)],
    )


def _param(value, location, name, finding_hash="p1"):
    return FindingView(
        finding_hash=finding_hash,
        type="param",
        value=value,
        path="input.js",
        severity=None,
        attributes={"location": location, "name": name},
        first_stage="analyzing",
        occurrences=[],
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
    assert req.endpoint_hashes == ("e1",)


def test_apply_probe_host_resolves_only_hostless_probeable_requests():
    # QA #2: the probe host-selector fills in a host for RELATIVE (host-less) requests
    # only; a request with a real occurrence host, and a non-probeable websocket, are
    # left untouched so the honest attribution and the confirmed hosts stay intact.
    reqs = reconstruct.build_requests(
        [
            _endpoint("GET /api/rel", host=None, raw_url="/api/rel", finding_hash="e1"),
            _endpoint(
                "GET /api/abs",
                host="api.acme.io",
                raw_url="https://api.acme.io/api/abs",
                finding_hash="e2",
            ),
            _endpoint("WS /socket", host=None, raw_url="/socket", finding_hash="e3"),
        ]
    )
    before = {r.operation: r for r in reqs}
    assert before["GET /api/rel"].hosts == ()  # relative -> host-less
    assert before["WS /socket"].probeable is False

    after = {r.operation: r for r in reconstruct.apply_probe_host(reqs, "chosen.example.com")}
    assert after["GET /api/rel"].hosts == ("chosen.example.com",)  # resolved against the pick
    assert after["GET /api/abs"].hosts == ("api.acme.io",)  # real host preserved
    assert after["WS /socket"].hosts == ()  # non-probeable untouched


def test_apply_probe_host_normalizes_and_ignores_blank():
    reqs = reconstruct.build_requests([_endpoint("GET /api/rel", host=None, raw_url="/api/rel")])
    # A full URL or a mixed-case / trailing-dot FQDN normalizes to a bare host.
    assert reconstruct.apply_probe_host(reqs, "https://Api.Example.com./x")[0].hosts == (
        "api.example.com",
    )
    # A blank / uninterpretable host is a no-op — the artifact keeps its {{base_url}}.
    assert reconstruct.apply_probe_host(reqs, "")[0].hosts == ()
    assert reconstruct.apply_probe_host(reqs, None)[0].hosts == ()
    assert reconstruct.apply_probe_host(reqs, "http://")[0].hosts == ()


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
    # endpoint_hashes is deterministic: sorted tuple of every contributing endpoint's hash
    assert req.endpoint_hashes == ("e1", "e2")


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


def test_build_groups_multiple_query_variants_into_one_operation():
    """MED-2: distinct query-key variants of the same operation are one triage
    unit — endpoint_hashes carries every contributing finding_hash, sorted."""
    findings = [
        _endpoint(
            "GET /search?q", host="api.acme.io", raw_url="/search?q=shoes", finding_hash="e1"
        ),
        _endpoint(
            "GET /search?page&q",
            host="api.acme.io",
            raw_url="/search?page=2&q=shoes",
            finding_hash="e2",
        ),
    ]
    reqs = reconstruct.build_requests(findings)
    assert len(reqs) == 1
    assert reqs[0].endpoint_hashes == ("e1", "e2")


def test_build_omits_content_type_for_jquery_body():
    """MED-3: jQuery `data` is form-urlencoded, not JSON — asserting
    application/json would be a lie the artifact then ships to the target."""
    findings = [
        FindingView(
            finding_hash="e1",
            type="endpoint",
            value="POST /api/users/{id}",
            path="input.js",
            severity=None,
            attributes={"method": "POST", "kind": "jquery"},
            first_stage="analyzing",
            occurrences=[_occ(host="api.acme.io", raw_url="/api/users/42")],
        ),
        _param("POST /api/users/{id} body:name", "body", "name"),
    ]
    (req,) = reconstruct.build_requests(findings)
    assert req.body_params == ("name",)
    assert req.content_type is None
