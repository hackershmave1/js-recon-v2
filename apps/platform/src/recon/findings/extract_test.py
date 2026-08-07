"""Colocated tests for the in-process JS network-call extractor (Vespasian).

Pure unit tests — parse JS strings, assert the reconstructed calls. No infra.
"""

from __future__ import annotations

from recon.findings.extract import _PARSER, collect_base_env, extract


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


# --- base-environment collection (Task 1: scope-safe pre-pass) ---------------
# Pure pre-pass: records only statically-certain, unshadowed base-URL bindings.
# Not yet wired into `extract()` — that's Task 2.


def _env(src: str):
    return collect_base_env(_PARSER.parse(src.encode()).root_node, src.encode())


def test_collect_base_env_axios_create_literal():
    env = _env("const loc = axios.create({ baseURL: '/location' });")
    assert env.instances == {"loc": "/location"}


def test_collect_base_env_defaults_and_const_prefix():
    env = _env("axios.defaults.baseURL = 'https://h/api'; const API = '/v3';")
    assert env.default_base == "https://h/api"
    assert env.const_prefixes["API"] == "/v3"


def test_collect_base_env_unknown_base_is_none_not_dropped():
    env = _env("const c = window.cfg; const loc = axios.create({ baseURL: c });")
    assert env.instances["loc"] is None  # recognized instance, base unknown


def test_collect_base_env_scope_collision_poisons_name():
    env = _env(
        "const loc = axios.create({ baseURL: '/a' }); items.forEach((loc) => loc.get('/x'));"
    )
    assert "loc" not in env.instances  # param `loc` shadows -> unresolvable


def test_collect_base_env_reassignment_poisons_name():
    env = _env("let loc = axios.create({ baseURL: '/a' }); loc = other;")
    assert "loc" not in env.instances


def test_collect_base_env_function_declaration_poisons_name():
    # The brief's own interface note lists "function declaration" as a
    # shadowing source alongside params/redeclaration — a later `function
    # loc(){}` must poison an earlier `const loc = axios.create(...)` too.
    env = _env("const loc = axios.create({ baseURL: '/a' }); function loc() {}")
    assert "loc" not in env.instances


# --- fix round 1: shadows introduced via destructuring/default/rest params ---
# Review finding: `_declared_names` only recognized a plain `identifier` as a
# binding, so a name shadowed via destructuring, a default, or a rest param
# escaped poisoning — `collect_base_env` would then guess a base for it,
# violating the "never guess a base" invariant.


def test_collect_base_env_destructured_param_poisons_name():
    # `{ loc }` is a destructured parameter — a shorthand `loc` inside it
    # shadows the outer `loc` axios instance just as a bare param would.
    env = _env(
        "const loc = axios.create({ baseURL: '/a' }); "
        "function useApi({ loc }) { return loc.get('/b'); }"
    )
    assert "loc" not in env.instances


def test_collect_base_env_destructured_declaration_poisons_name():
    # `const { loc } = require(...)` redeclares `loc` via destructuring —
    # must poison exactly like `const loc = require(...)` would.
    env = _env("const loc = axios.create({ baseURL: '/a' }); const { loc } = require('./x');")
    assert "loc" not in env.instances


def test_collect_base_env_default_param_poisons_name():
    # `function f(loc = 1)` binds `loc` via an `assignment_pattern` — the
    # default value itself (`1`) must not be mistaken for a binding.
    env = _env("const loc = axios.create({ baseURL: '/a' }); function f(loc = 1) {}")
    assert "loc" not in env.instances


def test_collect_base_env_rest_param_poisons_name():
    # `function f(...loc)` binds `loc` via a `rest_pattern`.
    env = _env("const loc = axios.create({ baseURL: '/a' }); function f(...loc) {}")
    assert "loc" not in env.instances


# --- base-URL joining at the sink (Task 2) ------------------------------------
# `collect_base_env`'s output wired into `extract()`: instance calls, bare
# axios+defaults, and `${CONST}` template prefixes should all produce full
# paths; an unknown-base instance is attributed (relative), never dropped;
# `.open` on any receiver still routes to XHR, not an axios join.


def _urls(src: str):
    return [(e.method, e.url) for e in extract(src).endpoints]


def test_axios_create_instance_call_joins_base():
    assert ("POST", "/location/address/search") in _urls(
        "const loc = axios.create({ baseURL: '/location' }); loc.post('/address/search', b);"
    )


def test_axios_defaults_base_joins_bare_call():
    assert ("GET", "https://h/api/pets") in _urls(
        "axios.defaults.baseURL = 'https://h/api'; axios.get('/pets');"
    )


def test_const_prefix_template_folds():
    assert ("GET", "/v3/pets") in _urls("const API = '/v3'; fetch(`${API}/pets`);")


def test_unknown_base_instance_attributed_relative_not_dropped():
    # recognized instance, base unknown -> endpoint present with the relative path
    assert ("GET", "/x") in _urls(
        "const c = w.c; const a = axios.create({ baseURL: c }); a.get('/x');"
    )


def test_absolute_url_ignores_base():
    assert ("GET", "https://other/z") in _urls(
        "const loc = axios.create({ baseURL: '/location' }); loc.get('https://other/z');"
    )


def test_open_on_instance_still_routes_to_xhr():
    # `.open(METHOD, url)` on any receiver keeps the XHR shape, not axios-join
    assert ("GET", "/raw") in _urls(
        "const loc = axios.create({ baseURL: '/location' }); loc.open('GET', '/raw');"
    )


# --- fix round 1: Task-2 review findings on the URL-resolution helpers -------
# 1. (Important) `_fold_const_prefix` concatenated `prefix + remainder` directly,
#    bypassing `_join_base`'s de-dupe — a prefix stored with a trailing slash
#    (`const API = '/v3/'`) doubled the slash (`/v3//pets`).
# 2. (Minor) `_join_base` detected "absolute" via a bare `"://" in path`
#    substring test, so a *relative* path that merely embeds a URL later on
#    (e.g. a redirect query param) was wrongly treated as absolute and the
#    base was silently dropped instead of joined.


def test_const_prefix_trailing_slash_does_not_double_up():  # review Important
    assert ("GET", "/v3/pets") in _urls("const API = '/v3/'; fetch(`${API}/pets`);")


def test_relative_path_with_url_in_query_still_joins_base():  # review Minor
    assert ("GET", "/location/redirect?next=http://evil.com") in _urls(
        "const loc = axios.create({ baseURL: '/location' }); "
        "loc.get('/redirect?next=http://evil.com');"
    )


def test_const_prefix_fold_generalizes_to_axios_get():  # generalization coverage
    # Same fold-then-join machinery (`_resolve_url` -> `_fold_const_prefix` ->
    # `_join_base`) reused by `_axios_member`, not just `_fetch` — guards both
    # the Important fix above and Task 2's 4-handler extension together.
    assert ("GET", "/v3/pets") in _urls("const API='/v3'; axios.get(`${API}/pets`);")


# --- fix round 2: `_fold_const_prefix` must concatenate, not base-join --------
# Round 1 fixed the trailing-slash double-up by delegating to `_join_base`, but
# `_join_base` ALWAYS inserts a separating `/` for a non-absolute remainder —
# correct for joining a *base URL* to a *path*, wrong for folding a template
# literal, where JS just concatenates strings: `'/v' + '2/pets'` is `/v2/pets`,
# not `/v/2/pets`. `_join_base` remains correct and untouched for its actual
# base/instance-join callers; only `_fold_const_prefix`'s use of it was wrong.


def test_const_prefix_folds_as_plain_concatenation():  # RED against round-1 code
    # JS evaluates `` `${API}2/pets` `` with API='/v' as `'/v' + '2/pets'`
    # = "/v2/pets". Round-1 code routes through `_join_base`, which inserts a
    # slash the remainder never had, yielding the wrong endpoint "/v/2/pets".
    assert ("GET", "/v2/pets") in _urls("const API = '/v'; fetch(`${API}2/pets`);")


def test_const_prefix_substitution_only_stays_verbatim():  # RED against round-1 code
    # No trailing text after the substitution -> the fold must reproduce the
    # constant verbatim ("/v3"), not append a slash ("/v3/") that `_join_base`
    # would insert for an empty remainder.
    assert ("GET", "/v3") in _urls("const API = '/v3'; fetch(`${API}`);")


# --- taught wrapper recognition (Task 1) -------------------------------------
# A named wrapper callee's member calls are recognized via the existing axios
# path, tagged with `RawEndpoint.wrapper`; `kind` stays "axios". Dispatch order
# is load-bearing: a callee colliding with a native target keeps the native path.

from recon.findings.wrappers import WrapperRule  # noqa: E402


def _wrapped(src: str, callees: list[str]):
    return extract(src, wrappers=[WrapperRule(c) for c in callees]).endpoints


def test_wrapper_member_call_is_recognized():
    eps = _wrapped("const api = makeClient(); api.get('/users');", ["api"])
    assert len(eps) == 1
    assert (eps[0].kind, eps[0].method, eps[0].url, eps[0].wrapper) == (
        "axios",
        "GET",
        "/users",
        "api",
    )


def test_wrapper_request_config_is_recognized():
    # `api.request({url, method})` falls out of the axios reuse for free (spec §4/§12 Minor 6).
    eps = _wrapped("api.request({url:'/x', method:'post'});", ["api"])
    assert (eps[0].method, eps[0].url, eps[0].wrapper) == ("POST", "/x", "api")


def test_dotted_receiver_wrapper_is_recognized():
    # A dotted receiver (`this.httpClient`) — the common minified class-based client
    # shape — resolves through the same object-text match, no extract.py change: the
    # dispatch already compares the full `this.httpClient` (spec §4 fast-follow).
    eps = _wrapped(
        "this.httpClient.request({url:'/api/v2/users/${e}', method:'get'});",
        ["this.httpClient"],
    )
    assert (eps[0].method, eps[0].url, eps[0].wrapper) == (
        "GET",
        "/api/v2/users/${e}",
        "this.httpClient",
    )


def test_wrapper_post_body_params_are_mined():
    eps = _wrapped("api.post('/login', {user:1});", ["api"])
    assert ("user", "body") in {(p.name, p.location) for p in eps[0].params}


def test_untaught_wrapper_still_leaves_no_trace():
    # Regression: without a rule, a wrapper call is dropped exactly as today.
    result = extract("api.get('/users');")
    assert result.endpoints == [] and result.unattributed == 0


def test_native_axios_collision_takes_native_path_not_wrapper():
    # `axios` taught as a callee must still resolve via the native branch
    # (dispatch-last), so NO wrapper tag is attached (spec §4/§12 Minor 7).
    eps = _wrapped("axios.get('/x');", ["axios"])
    assert (eps[0].kind, eps[0].url, eps[0].wrapper) == ("axios", "/x", None)


def test_axios_create_instance_collision_keeps_base_not_wrapper():
    # An axios.create instance var named like the wrapper keeps its real base
    # (instance branch precedes the wrapper branch); tag stays None, base applies.
    eps = _wrapped("const api = axios.create({baseURL:'/b'}); api.get('/x');", ["api"])
    assert ("GET", "/b/x", None) in [(e.method, e.url, e.wrapper) for e in eps]


def test_wrapper_dynamic_arg_is_unattributed_like_axios():
    # A non-static URL leaves the same honest trace axios would (REQ-C2).
    result = extract("api.get(dynamicUrl);", wrappers=[WrapperRule("api")])
    assert result.endpoints == [] and result.unattributed == 1
