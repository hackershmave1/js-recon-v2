from recon.correlate.match import Endpoint, correlate


def _ep(finding_hash, method, path):
    return Endpoint(finding_hash=finding_hash, method=method, path=path)


def test_leading_var_host_template_resolves_from_observed_url():
    # The flagship: GET /${baseDomainName}/get-job-types + an observed request to the
    # real host -> the real URL, host recovered from ground truth.
    endpoints = [_ep("h1", "GET", "/${baseDomainName}/get-job-types")]
    requests = [{"method": "GET", "url": "https://api.acme.io/get-job-types"}]
    assert correlate(endpoints, requests) == {"h1": "https://api.acme.io/get-job-types"}


def test_bare_relative_endpoint_resolves():
    endpoints = [_ep("h2", "POST", "/getJobId")]
    requests = [{"method": "POST", "url": "https://api.acme.io/getJobId"}]
    assert correlate(endpoints, requests) == {"h2": "https://api.acme.io/getJobId"}


def test_rooted_constant_path_must_match_exactly_no_prefix_grab():
    # FP guard #1: /users must NOT grab an observed /admin/users (a different endpoint).
    endpoints = [_ep("h", "GET", "/users")]
    requests = [{"method": "GET", "url": "https://api.acme.io/admin/users"}]
    assert correlate(endpoints, requests) == {}


def test_alignment_is_positional_not_subsequence():
    # FP guard #2: /orders/{id}/items aligns to /orders/42/items positionally, and must
    # NOT match /orders/x/y/items/z (a subsequence that shares the same tokens in order).
    ep = [_ep("h", "GET", "/orders/{id}/items")]
    assert correlate(ep, [{"method": "GET", "url": "https://a.io/orders/42/items"}]) == {
        "h": "https://a.io/orders/42/items"
    }
    assert correlate(ep, [{"method": "GET", "url": "https://a.io/orders/x/y/items/z"}]) == {}


def test_most_specific_finding_wins_contested_url():
    # A rooted exact match beats a leading-var match that swallowed the same tail: the
    # observed /orders/42/items belongs to /orders/{id}/items, not /${host}/items.
    endpoints = [
        _ep("specific", "GET", "/orders/{id}/items"),
        _ep("greedy", "GET", "/${host}/items"),
    ]
    requests = [{"method": "GET", "url": "https://a.io/orders/42/items"}]
    assert correlate(endpoints, requests) == {"specific": "https://a.io/orders/42/items"}


def test_template_without_constant_anchor_is_never_matched():
    # A path of only vars/params has no anchor to correlate on -> never resolved.
    endpoints = [_ep("h", "GET", "/${a}/{id}")]
    requests = [{"method": "GET", "url": "https://a.io/x/y"}]
    assert correlate(endpoints, requests) == {}


def test_finding_matching_two_hosts_is_skipped_as_ambiguous():
    endpoints = [_ep("h", "GET", "/status")]
    requests = [
        {"method": "GET", "url": "https://a.io/status"},
        {"method": "GET", "url": "https://b.io/status"},
    ]
    assert correlate(endpoints, requests) == {}


def test_method_must_match():
    endpoints = [_ep("h", "GET", "/getJobId")]
    requests = [{"method": "POST", "url": "https://a.io/getJobId"}]
    assert correlate(endpoints, requests) == {}


def test_param_segment_matches_a_concrete_value():
    endpoints = [_ep("h", "GET", "/orders/{id}")]
    requests = [{"method": "GET", "url": "https://a.io/orders/42"}]
    assert correlate(endpoints, requests) == {"h": "https://a.io/orders/42"}


def test_equally_specific_contested_url_is_dropped():
    # Two findings match the observed URL with identical specificity -> ambiguous tie ->
    # neither resolved (conservative honesty).
    endpoints = [
        _ep("a", "GET", "/users"),
        _ep("b", "GET", "/${host}/users"),  # tail [users], absorbed 0 on a 1-seg path
    ]
    requests = [{"method": "GET", "url": "https://a.io/users"}]
    assert correlate(endpoints, requests) == {}


def test_leading_var_absorbs_a_base_path_prefix():
    # The ${var} legitimately stands for host + a base path segment.
    endpoints = [_ep("h", "GET", "/${apiBase}/get-job-types")]
    requests = [{"method": "GET", "url": "https://api.acme.io/v2/get-job-types"}]
    assert correlate(endpoints, requests) == {"h": "https://api.acme.io/v2/get-job-types"}


def test_empty_inputs():
    assert correlate([], [{"method": "GET", "url": "https://a.io/x"}]) == {}
    assert correlate([_ep("h", "GET", "/x")], []) == {}
