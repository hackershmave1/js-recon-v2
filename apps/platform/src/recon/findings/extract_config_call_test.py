"""Config-object client calls: `<any receiver>.<any method>({method: "<verb>", url})`.

A real SPA routed ~88% of its API paths through `this.httpClient.request({method, url})` on a
client injected via `Object.assign(this, deps)` — nothing proved the receiver an HTTP client, so
every call was dropped. The `{method: <HTTP verb literal>, url}` object is itself the evidence.
"""

from __future__ import annotations

from recon.findings.extract import extract

_INJECTED = "class S{constructor(e){Object.assign(this,e)}%s}"


def _eps(src: str) -> list[tuple[str, str]]:
    return [(e.method, e.url) for e in extract(src).endpoints]


def test_injected_client_request_config_is_an_endpoint():
    src = (
        _INJECTED
        % 'getUser(e){return this.httpClient.request({method:"get",url:`/api/v2/users/${e}`})}'
    )
    [(method, url)] = _eps(src)
    assert method == "GET"
    assert url.startswith("/api/v2/users/")


def test_any_method_name_and_minified_receiver():
    src = (
        'this.apiService.apiRequest({method:"post",url:"/api/v2/notes/fetch"});'
        'this.cxRequest({method:"patch",url:"/api/v2/company/settings"});'
        'n.request({method:"DELETE",url:"/api/v2/session"});'
    )
    assert sorted(_eps(src)) == [
        ("DELETE", "/api/v2/session"),
        ("PATCH", "/api/v2/company/settings"),
        ("POST", "/api/v2/notes/fetch"),
    ]


def test_params_are_query_and_data_is_body():
    src = 'this.httpClient.request({method:"put",url:"/api/v2/account/settings",params:{q:1},data:{name:"n"}})'
    [ep] = extract(src).endpoints
    assert {(p.name, p.location) for p in ep.params} >= {("q", "query"), ("name", "body")}


def test_computed_url_is_a_counted_unresolved_row_not_an_endpoint():
    result = extract('this.httpClient.request({method:"get",url:buildUrl(e)})')
    assert result.endpoints == []
    assert len(result.unresolved) == 1
    assert result.unresolved[0].method == "GET"


def test_non_literal_method_is_not_evidence():
    # A wrapper's own body forwards caller-supplied values — that is not a request site.
    assert _eps("request(e){return this.http.request({method:e.method,url:e.url})}") == []


def test_needs_both_keys_and_a_real_verb():
    assert _eps('this.store.request({url:"/api/x"})') == []
    assert _eps('this.store.request({method:"get"})') == []
    assert _eps('this.store.request({method:"fetchAll",url:"/api/x"})') == []


def test_axios_request_config_is_not_double_emitted():
    assert _eps('axios.request({url:"/api/x", method:"patch"})') == [("PATCH", "/api/x")]


def test_non_client_method_names_are_not_requests():
    # Design review #1: the same {method,url} shape is data, a route table or a mock elsewhere.
    for src in (
        'session.observations.push({method:"GET",url:"/api/x"})',
        'fastify.route({method:"GET",url:"/api/items",handler:h})',
        'fetchMock.mock({method:"GET",url:"/api/users"},200)',
        'this.logger.warn({method:"POST",url:t,status:r.status})',
    ):
        result = extract(src)
        assert (result.endpoints, result.unresolved, result.unattributed) == ([], [], 0), src


def test_known_instance_config_call_keeps_its_base():
    # Design review #2: an inferred instance's non-verb method used to be dropped with no trace.
    src = 'const api=T.create({baseURL:"https://h.io"});api.apiRequest({method:"get",url:"/v1/x"})'
    [(method, url)] = _eps(src)
    assert method == "GET"
    assert url.endswith("/v1/x")


def test_unknown_client_kind_does_not_claim_a_json_body():
    # Design review #3: kind "axios" makes the probe assert application/json.
    [ep] = extract('this.httpClient.request({method:"post",url:"/api/x",data:{a:1}})').endpoints
    assert ep.kind == "client"


def test_body_key_is_body_params():
    [ep] = extract('this.httpClient.request({method:"post",url:"/api/x",body:{b:2}})').endpoints
    assert ("b", "body") in {(p.name, p.location) for p in ep.params}


def test_shorthand_url_is_an_unresolved_row():
    result = extract('this.httpClient.request({method:"get",url})')
    assert result.endpoints == []
    assert [u.method for u in result.unresolved] == ["GET"]
