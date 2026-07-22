from recon.probe import serialize
from recon.probe.reconstruct import QueryParam, ReconstructedRequest


def _req(**overrides):
    base = dict(
        operation="POST /api/users/{id}", method="POST", path="/api/users/{id}",
        hosts=("api.acme.io",), query_params=(), body_params=("amount",),
        content_type="application/json", example_url="/api/users/123",
        probeable=True, endpoint_hash="e1",
    )
    base.update(overrides)
    return ReconstructedRequest(**base)


def test_curl_uses_concrete_example_url_and_method():
    out = serialize.to_curl(_req())
    assert "curl -X POST" in out
    assert "'https://api.acme.io/api/users/123'" in out
    assert "-H 'Content-Type: application/json'" in out
    assert '--data \'{"amount":"<amount>"}\'' in out
    assert "# add auth/headers here" in out


def test_curl_falls_back_to_base_url_placeholder_when_no_host():
    out = serialize.to_curl(_req(hosts=(), example_url="/x"))
    assert "{{base_url}}/x" in out


def test_curl_shell_quotes_hostile_url():
    # A hostile path must be quoted as a single shell token, never executed.
    out = serialize.to_curl(_req(example_url="/a; rm -rf /", body_params=(), content_type=None))
    assert "'https://api.acme.io/a; rm -rf /'" in out


def test_http_strips_crlf_injection_from_target():
    out = serialize.to_http(_req(example_url="/a\r\nX-Evil: 1", body_params=(), content_type=None))
    assert "\r" not in out
    assert "\nX-Evil:" not in out  # the injected header never became its own line


def test_websocket_request_has_no_artifacts():
    req = _req(operation="WSS /socket", method="WSS", path="/socket", probeable=False)
    assert serialize.to_curl(req) is None
    assert serialize.to_http(req) is None


def test_http_has_request_line_host_and_json_body():
    out = serialize.to_http(_req())
    assert out.startswith("POST /api/users/123 HTTP/1.1")
    assert "Host: api.acme.io" in out
    assert "Content-Type: application/json" in out
    assert '{"amount":"<amount>"}' in out


def test_http_strips_crlf_injection_from_method():
    # CRITICAL: method is attacker-controlled (JS fetch/axios literals).
    # A hostile method must have CR/LF stripped to prevent header injection.
    out = serialize.to_http(_req(method="GET\r\nX-Injected: 1"))
    # After stripping CR/LF, the method becomes "GETX-Injected: 1" (control chars removed).
    # The key is: no CR/LF characters in output (injection prevented).
    assert "\r" not in out
    assert "\nX-Injected:" not in out  # the injected header never became its own line
    # Verify no standalone newline in the request line (single line still intact)
    lines = out.split("\n")
    request_line = lines[0]
    # The request line should not contain a bare CR
    assert "\r" not in request_line


def test_curl_caps_oversized_url():
    # IMPORTANT: hosts[0] is attacker-controlled (JS string literal).
    # An oversized host must be capped to prevent unbounded artifact size.
    huge_host = "a" * 100000
    out = serialize.to_curl(_req(hosts=(huge_host,), example_url="/x"))
    # Verify curl output is bounded (well under 20000 chars)
    assert len(out) < 20000
    # Verify the URL itself is capped to _MAX_URL
    assert huge_host not in out  # the oversized host should not appear in full


def test_curl_neutralizes_hostile_method():
    # Cross-cover: verify to_curl also strips method CR/LF (was only tested in to_http).
    out = serialize.to_curl(_req(method="GET\r\nX-Injected: 1", body_params=(), content_type=None))
    assert "\r" not in out
    assert "\nX-Injected:" not in out  # the injected header never became its own line


def test_http_caps_oversized_host():
    # Cross-cover: verify to_http also caps oversized hosts (was only tested in to_curl).
    huge_host = "a" * 100000
    out = serialize.to_http(_req(hosts=(huge_host,), example_url="/x"))
    # Verify http output is bounded (well under 9000 chars, much less than the 100k host)
    assert len(out) < 9000
    assert huge_host not in out


def test_curl_caps_oversized_other_hosts():
    # Verify Fix A#1: "other hosts" line is capped.
    huge_host2 = "b" * 100000
    out = serialize.to_curl(_req(hosts=("a.com", huge_host2), example_url="/x"))
    # Verify curl output is bounded (well under 20000 chars)
    assert len(out) < 20000
    # Verify the huge secondary host is not fully present
    assert huge_host2 not in out


def test_curl_absolute_example_url_no_double_scheme():
    # example_url is the raw JS literal and may already be absolute — the host
    # must come from it directly, never re-prepended (was: double-scheme bug).
    out = serialize.to_curl(
        _req(hosts=("api.x.com",), example_url="https://api.x.com/v1/users", body_params=(), content_type=None)
    )
    assert "'https://api.x.com/v1/users'" in out
    assert "api.x.comhttps://" not in out


def test_http_absolute_example_url_uses_origin_form():
    out = serialize.to_http(
        _req(
            hosts=("api.x.com",), example_url="https://api.x.com/v1/users",
            body_params=(), content_type=None, method="GET",
        )
    )
    assert out.startswith("GET /v1/users HTTP/1.1")
    assert "Host: api.x.com" in out
    request_line = out.split("\n")[0]
    assert "https://" not in request_line
