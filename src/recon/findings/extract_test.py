"""Colocated tests for the in-process JS network-call extractor (Vespasian).

Pure unit tests — parse JS strings, assert the reconstructed calls. No infra.
"""

from __future__ import annotations

from recon.findings.extract import extract


def _only(source: str):
    result = extract(source)
    assert len(result.endpoints) == 1, result.endpoints
    return result.endpoints[0]


def test_fetch_literal_defaults_to_get():
    ep = _only('fetch("/api/users");')
    assert (ep.kind, ep.method, ep.url) == ("fetch", "GET", "/api/users")


def test_fetch_reads_method_and_body_params():
    ep = _only('fetch("/api/login", {method:"post", body:{user:1, pass:2}})')
    assert ep.method == "POST"
    assert {(p.name, p.location) for p in ep.params} == {("user", "body"), ("pass", "body")}


def test_fetch_extracts_query_params():
    ep = _only('fetch("/search?q=hi&page=2")')
    assert {p.name for p in ep.params if p.location == "query"} == {"q", "page"}


def test_fetch_template_string_keeps_shape():
    ep = _only("fetch(`/api/users/${id}/orders`)")
    assert ep.url == "/api/users/${id}/orders"


def test_window_fetch_is_detected():
    ep = _only('window.fetch("/a")')
    assert ep.kind == "fetch"


def test_xhr_open_captures_method_and_url():
    ep = _only('var x = new XMLHttpRequest(); x.open("DELETE", "/api/item/9");')
    assert (ep.kind, ep.method, ep.url) == ("xhr", "DELETE", "/api/item/9")


def test_non_http_open_is_ignored():
    # `.open(...)` whose first arg isn't an HTTP method is not an XHR call.
    assert extract('modal.open("settings", "/foo")').endpoints == []


def test_axios_config_object():
    ep = _only('axios({url:"/api/v2/things", method:"put", data:{a:1}})')
    assert (ep.kind, ep.method, ep.url) == ("axios", "PUT", "/api/v2/things")
    assert ("a", "body") in {(p.name, p.location) for p in ep.params}


def test_axios_method_shorthand():
    ep = _only('axios.get("/api/profile")')
    assert (ep.method, ep.url) == ("GET", "/api/profile")


def test_axios_request_config():
    ep = _only('axios.request({url:"/api/x", method:"patch"})')
    assert (ep.method, ep.url) == ("PATCH", "/api/x")


def test_jquery_ajax_config():
    ep = _only('$.ajax({url:"/api/save", type:"POST", data:{name:"n"}})')
    assert (ep.kind, ep.method, ep.url) == ("jquery", "POST", "/api/save")
    assert ("name", "body") in {(p.name, p.location) for p in ep.params}


def test_jquery_get_and_post_shorthands():
    result = extract('$.get("/a"); jQuery.post("/b");')
    by_url = {ep.url: ep.method for ep in result.endpoints}
    assert by_url == {"/a": "GET", "/b": "POST"}


def test_websocket_scheme_becomes_method():
    assert _only('new WebSocket("ws://x/ws")').method == "WS"
    assert _only('new WebSocket("wss://x/ws")').method == "WSS"


def test_dynamic_url_is_counted_not_invented():
    # A bare variable / concatenation is unattributed, not guessed (REQ-C2 honesty).
    result = extract('fetch(userSuppliedUrl); fetch("/base/" + segment);')
    assert result.endpoints == []
    assert result.unattributed == 2


def test_mixed_bundle_finds_all_sinks():
    source = (
        'fetch("/a");'
        'axios.post("/b", {});'
        'var r=new XMLHttpRequest();r.open("GET","/c");'
        '$.getJSON("/d");'
        'new WebSocket("wss://e/ws");'
    )
    result = extract(source)
    assert {ep.url for ep in result.endpoints} == {"/a", "/b", "/c", "/d", "wss://e/ws"}
    assert result.unattributed == 0


def test_line_number_is_one_based():
    ep = _only('\n\nfetch("/a");')
    assert ep.line == 3


# --- regressions from the code review ----------------------------------------

def test_axios_params_config_is_query_not_body():  # review HIGH-1
    ep = _only('axios({url:"/s", method:"get", params:{a:1}, data:{b:2}})')
    locations = {(p.name, p.location) for p in ep.params}
    assert ("a", "query") in locations
    assert ("b", "body") in locations
    assert ("a", "body") not in locations


def test_computed_member_sinks_are_detected():  # review HIGH-2 (C2 honesty)
    assert _only('axios["get"]("/x")').url == "/x"
    assert _only('window["fetch"]("/y")').kind == "fetch"
    xhr = _only('var r=new XMLHttpRequest(); r["open"]("POST","/z");')
    assert (xhr.kind, xhr.method, xhr.url) == ("xhr", "POST", "/z")


def test_axios_shorthand_mines_body_and_query_params():  # review MEDIUM-2
    post = _only('axios.post("/x", {name:1, email:2})')
    assert {(p.name, p.location) for p in post.params} == {("name", "body"), ("email", "body")}
    get = _only('axios.get("/s", {params:{page:1}})')
    assert ("page", "query") in {(p.name, p.location) for p in get.params}


def test_jquery_shorthand_mines_data():  # review MEDIUM-2
    post = _only('$.post("/x", {name:1})')
    assert ("name", "body") in {(p.name, p.location) for p in post.params}
    get = _only('$.get("/s", {q:1})')
    assert ("q", "query") in {(p.name, p.location) for p in get.params}


def test_json_stringify_body_is_mined():  # review MEDIUM-3
    ep = _only('fetch("/x", {method:"POST", body:JSON.stringify({a:1, b:2})})')
    assert {(p.name, p.location) for p in ep.params} == {("a", "body"), ("b", "body")}
