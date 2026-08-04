from dataclasses import replace

from recon.findings.base_url import BaseUrlRule
from recon.probe.reconstruct import ReconstructedRequest, build_requests


def _view(finding_hash, type_, value, attributes=None, occurrences=()):
    # A tiny stand-in matching the fields build_requests reads off FindingView.
    from types import SimpleNamespace
    return SimpleNamespace(
        finding_hash=finding_hash, type=type_, value=value,
        attributes=attributes or {}, occurrences=list(occurrences),
    )


def _occ(host=None, raw_url=None):
    from types import SimpleNamespace
    return SimpleNamespace(host=host, raw_url=raw_url)


def _prefix(prefix, base):
    return BaseUrlRule(kind="prefix", base_url=base, path_prefix=prefix)


def test_prefix_rule_resolves_and_preserves_params():
    findings = [
        _view("h1", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"}, [_occ()]),
        _view("p1", "param", "GET /address/search query:page", {"location": "query", "name": "page"}),
    ]
    (req,) = build_requests(findings, [_prefix("/address", "/location")])
    assert req.path == "/location/address/search"
    assert req.operation == "GET /location/address/search"
    assert [p.name for p in req.query_params] == ["page"]  # param survived the re-key (gate B2)


def test_absolute_op_is_not_rebased():
    findings = [
        _view("h1", "endpoint", "GET /location/address/search",
              {"method": "GET", "kind": "fetch"}, [_occ(host="api.example.com",
              raw_url="https://api.example.com/location/address/search")]),
    ]
    (req,) = build_requests(findings, [_prefix("/location", "/wrong")])
    assert req.path == "/location/address/search"  # has a host -> candidate gate skips it


def test_host_bearing_base_sets_hosts_and_example_url():
    findings = [_view("h1", "endpoint", "GET /x", {"method": "GET", "kind": "fetch"}, [_occ()])]
    (req,) = build_requests(findings, [_prefix("/x", "https://api.example.com/v3")])
    assert req.path == "/v3/x"
    assert req.hosts == ("api.example.com",)
    assert req.example_url == "https://api.example.com/v3/x"


def test_collision_merges_relative_onto_absolute():
    findings = [
        _view("h1", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"},
              [_occ()]),
        _view("h2", "endpoint", "GET /location/address/search", {"method": "GET", "kind": "fetch"},
              [_occ(host="acme.io", raw_url="https://acme.io/location/address/search")]),
        _view("pa", "param", "GET /address/search query:a", {"location": "query", "name": "a"}),
        _view("pb", "param", "GET /location/address/search query:b", {"location": "query", "name": "b"}),
    ]
    reqs = build_requests(findings, [_prefix("/address", "/location")])
    (merged,) = [r for r in reqs if r.path == "/location/address/search"]
    names = {p.name for p in merged.query_params}
    assert {"a", "b"} <= names            # both operations' params survive the merge
    assert set(merged.endpoint_hashes) == {"h1", "h2"}


def test_input_order_is_deterministic():
    findings = [
        _view("h1", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"}, [_occ()]),
        _view("h2", "endpoint", "GET /location/address/search", {"method": "GET", "kind": "fetch"},
              [_occ(host="acme.io", raw_url="https://acme.io/location/address/search")]),
    ]
    rules = [_prefix("/address", "/location")]
    a = build_requests(list(findings), rules)
    b = build_requests(list(reversed(findings)), rules)
    assert [r.operation for r in a] == [r.operation for r in b]
    assert a == b


def test_no_rules_is_unchanged_behavior():
    findings = [_view("h1", "endpoint", "GET /a/b", {"method": "GET", "kind": "fetch"}, [_occ()])]
    (req,) = build_requests(findings)  # default rules=()
    assert req.path == "/a/b" and req.operation == "GET /a/b"


def test_mixed_relative_absolute_group_is_not_rebased():
    # Same op value from two files: one relative (no host), one absolute (host).
    # The op-group's host union is non-empty, so reconstruct's op-group gate treats
    # the whole operation as host-bearing and does NOT re-base it (export keeps the
    # observed path). Since B1 (REQ-C2 §9) classify uses the same op-group gate, so
    # within a run both sides now agree here (see base_url_classify_test.py's mixed-op
    # test); this pins reconstruct's half of that parity.
    findings = [
        _view("hA", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"}, [_occ()]),
        _view("hB", "endpoint", "GET /address/search", {"method": "GET", "kind": "fetch"},
              [_occ(host="acme.io", raw_url="https://acme.io/address/search")]),
    ]
    (req,) = build_requests(findings, [_prefix("/address", "/location")])
    assert req.path == "/address/search"          # not re-based (group has an absolute member)
    assert req.hosts == ("acme.io",)
    assert set(req.endpoint_hashes) == {"hA", "hB"}
