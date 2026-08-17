"""Colocated tests for the in-process JS network-call extractor (Vespasian).

Pure unit tests — parse JS strings, assert the reconstructed calls. No infra.
"""

from __future__ import annotations

import sys
import time

import pytest

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


# --- Tier 4: surface unresolved sinks (the unconfirmed lane) ------------------ #
# A detected sink whose URL isn't statically resolvable is no longer silently
# dropped: it still counts as `unattributed` (REQ-C2 honesty is unchanged) AND is
# surfaced in `unresolved` with a best-effort _collapse_url skeleton + evidence.


def test_fetch_variable_url_is_surfaced_as_unresolved():
    r = extract("fetch(u)")
    assert r.endpoints == []
    assert r.unattributed == 1  # counter unchanged — the call is still "unattributed"
    assert len(r.unresolved) == 1
    u = r.unresolved[0]
    assert (u.kind, u.method, u.url) == ("fetch", "GET", "EXPR")
    assert "fetch(u)" in u.snippet  # the real call is preserved as evidence


def test_fetch_concatenation_yields_a_path_skeleton():
    r = extract('fetch("/api/" + path)')
    assert r.endpoints == [] and len(r.unresolved) == 1
    assert r.unresolved[0].url == "/api/:path"  # static head kept, readable holder -> :path


def test_fetch_member_url_surfaced_with_method_from_options():
    r = extract('fetch(g.download_url, {method:"delete"})')
    assert len(r.unresolved) == 1
    assert r.unresolved[0].method == "DELETE" and r.unresolved[0].url == ":download_url"


def test_axios_member_variable_url_surfaced_with_verb_method():
    r = extract("axios.post(u, {a:1})")
    assert r.endpoints == [] and len(r.unresolved) == 1
    assert (r.unresolved[0].kind, r.unresolved[0].method) == ("axios", "POST")


def test_xhr_open_variable_url_surfaced():
    r = extract('var x = new XMLHttpRequest(); x.open("PUT", u)')
    assert len(r.unresolved) == 1
    assert (r.unresolved[0].kind, r.unresolved[0].method) == ("xhr", "PUT")


def test_resolved_call_is_not_also_surfaced_as_unresolved():
    r = extract('fetch("/api/users")')
    assert len(r.endpoints) == 1 and r.unresolved == [] and r.unattributed == 0


def test_multiple_unresolved_calls_all_surfaced_and_counted():
    r = extract("fetch(a); fetch(b);")
    assert len(r.unresolved) == 2 and r.unattributed == 2 and r.endpoints == []


def test_deeply_nested_concatenation_is_capped_not_a_recursion_error():
    # String-splitting ("x"+"x"+...) is a real static-analysis-evasion obfuscation; a
    # pathologically deep concat must degrade to a skeleton via the depth cap, never blow
    # the Python stack out of extract(). 2000 terms is well past the interpreter limit.
    r = extract("fetch(" + "+".join(['"x"'] * 2000) + ")")
    assert len(r.unresolved) == 1  # surfaced cleanly, no RecursionError


# --- .concat() reconstruction + value-holder tokens --------------------------- #
# `.concat()` is reconstructed exactly like `+`; a readable non-constant leaf renders as
# a `:holder` token (its source identifier) so an analyst sees WHICH value fills a
# dynamic segment, while minifier mangles (1-char / vowelless-2-char) stay EXPR.


def test_concat_at_sink_reconstructed_with_holder():
    r = extract('fetch("/api/users/".concat(userId))')
    assert r.endpoints == [] and len(r.unresolved) == 1
    assert r.unresolved[0].url == "/api/users/:userId"


def test_concat_chain_keeps_static_text_and_holders():
    r = extract('apiClient.get("/api/".concat(kind).concat("/list"))')
    assert r.generic[0].url == "/api/:kind/list"


def test_value_holder_uses_last_member_identifier():
    assert extract("fetch(g.downloadUrl)").unresolved[0].url == ":downloadUrl"


def test_minified_holder_stays_expr():
    assert extract("fetch(u)").unresolved[0].url == "EXPR"  # 1-char mangle
    assert extract('fetch("/x/".concat(xr))').unresolved[0].url == "/x/EXPR"  # vowelless 2-char


def test_same_origin_member_normalizes_to_rooted_path():
    # window.location.origin is the current origin -> "" so the following /path roots.
    r = extract('fetch("".concat(window.location.origin, "/player/").concat(playerId))')
    assert r.unresolved[0].url == "/player/:playerId"


def test_deeply_nested_concat_calls_are_capped():
    src = 'fetch("a"' + '.concat("b")' * 2000 + ")"
    assert len(extract(src).unresolved) == 1  # capped, no RecursionError


def test_array_concat_is_not_a_url_false_positive():
    # `.concat()` also builds ARRAYS; the resulting non-path skeleton is rejected by the
    # generic path gate, and is harmless as a Tier-4 skeleton (the fetch sink is real).
    assert extract('apiClient.get(arr.concat("/x"))').generic == []
    assert extract("fetch([x].concat(y))").unresolved[0].url == "EXPREXPR"


# --- Tier 5: generic-call (suspected untaught HTTP clients) ------------------- #
# A verb call `.get/.post/…` on an UNRECOGNISED but HTTP-client-shaped receiver is
# surfaced in `generic` (distinct from `unresolved`) as a SUSPECTED sink. It must NOT
# move the REQ-C2 coverage counters (suspected, not detected), and its two precision
# gates — a readable-receiver gate and a strict path-shape gate — must hold on the
# minified receivers and non-path args that dominate real bundles.


def test_generic_call_on_readable_client_is_surfaced():
    r = extract('apiClient.get("/api/users")')
    assert r.endpoints == [] and r.unattributed == 0  # not a detected sink -> counter untouched
    assert len(r.generic) == 1
    g = r.generic[0]
    assert (g.kind, g.method, g.url) == ("generic", "GET", "/api/users")
    assert "apiClient.get" in g.snippet  # the real call is preserved as evidence


def test_generic_call_reads_verb_as_method():
    assert extract('httpService.post("/api/login")').generic[0].method == "POST"
    assert extract('apiClient.delete("/api/users/1")').generic[0].method == "DELETE"
    assert extract('apiClient.put("/api/users/1")').generic[0].method == "PUT"


def test_generic_call_this_http_angular_pattern():
    # `this.http.get("/x")` — receiver text "this.http" carries the `http` hint.
    r = extract('this.http.get("/api/orders")')
    assert len(r.generic) == 1 and r.generic[0].url == "/api/orders"


def test_generic_call_keeps_template_and_concat_shape():
    assert extract("apiClient.get(`/api/users/${id}`)").generic[0].url == "/api/users/${id}"
    assert extract('apiClient.get("/api/" + id)').generic[0].url == "/api/:id"


def test_generic_call_absolute_url_is_surfaced():
    r = extract('apiClient.get("https://api.acme.io/v1/users")')
    assert len(r.generic) == 1 and r.generic[0].url == "https://api.acme.io/v1/users"


def test_generic_call_not_fired_on_minified_receiver():
    # A minified 1-2 char receiver defeats any word denylist but fails the readable-receiver
    # gate, so the flood of `n.get(...)`/`e.post(...)` in a mangled bundle never fires.
    assert extract('n.get("/api/users")').generic == []
    assert extract('e.post("/api/login")').generic == []
    assert extract('xr.get("/api/x")').generic == []


def test_generic_call_skips_known_non_http_receivers():
    # Readable but well-known NON-HTTP objects: Map/cache/store/router/searchParams/config, and
    # server-side route definers (app/server) — a `.get` here is not a client call.
    for src in (
        'cache.get("/api/x")',
        'store.get("/api/x")',
        'router.get("/api/x")',
        'searchParams.get("/api/x")',
        'config.get("/api/x")',
        'server.get("/api/x")',
    ):
        assert extract(src).generic == [], src


def test_generic_call_requires_path_shaped_argument():
    # readable client, but the arg has no path anchor -> rejected by the strict path gate
    # (stricter than jsluice MaybeURL: a relative or dotted string is not enough).
    assert extract('apiClient.get("userId")').generic == []  # bare word
    assert extract('apiClient.get("a.b.c")').generic == []  # dotted path (lodash-style)
    assert extract("apiClient.get(userVar)").generic == []  # bare variable -> :userVar, no anchor
    assert extract('apiClient.get("users/list")').generic == []  # relative, no leading slash


def test_real_sinks_and_instances_win_over_generic_call():
    # Every real sink / instance / jQuery / global fetch matches an earlier branch; a call they
    # own must NEVER also land in `generic`. axios.get(var) is Tier-4 unresolved, not generic.
    axios_var = extract("axios.get(u)")
    assert axios_var.generic == [] and len(axios_var.unresolved) == 1
    assert extract('$.get("/x")').generic == []
    assert extract('window.fetch("/x")').generic == []


def test_generic_call_computed_member_is_surfaced():
    # property-mangled bundles use computed access; `apiClient["get"]("/x")` reaches the generic
    # branch via _handle_call's subscript path, exactly like a dotted `.get`.
    r = extract('apiClient["get"]("/api/x")')
    assert len(r.generic) == 1 and r.generic[0].method == "GET"


def test_generic_call_skips_dotted_non_http_receiver():
    # A denylisted object reached through a member chain (`this.store`, `this.cache`) is excluded
    # by matching the LAST dotted segment — while a dotted HTTP client (`this.http`) still fires.
    assert extract('this.store.get("/api/x")').generic == []
    assert extract('this.cache.get("/api/x")').generic == []
    assert len(extract('this.http.get("/api/x")').generic) == 1


# --- enrichment B: auth header capture --------------------------------------- #


def _headers(ep):
    return [(h.name, h.scheme) for h in ep.headers]


def test_fetch_captures_bearer_authorization_header():
    ep = _only('fetch("/api/me", {headers:{Authorization:"Bearer " + token}})')
    assert _headers(ep) == [("Authorization", "bearer")]


def test_fetch_dynamic_header_value_keeps_name_drops_scheme():
    ep = _only('fetch("/api/me", {headers:{Authorization: token}})')
    assert _headers(ep) == [("Authorization", None)]


def test_non_auth_header_is_not_captured():
    ep = _only('fetch("/api/me", {headers:{"Content-Type":"application/json"}})')
    assert ep.headers == ()


def test_axios_get_config_headers_captured():
    # M5: axios.get(url, {headers}) dispatches through _axios_member's config arg.
    ep = _only('axios.get("/api/me", {headers:{"X-API-Key":"k"}})')
    assert _headers(ep) == [("X-API-Key", None)]


def test_axios_post_config_headers_captured():
    ep = _only('axios.post("/api/x", {a:1}, {headers:{Authorization:"Bearer " + t}})')
    assert _headers(ep) == [("Authorization", "bearer")]


def test_axios_config_form_basic_scheme():
    ep = _only('axios({url:"/api/me", headers:{Authorization:"Basic " + creds}})')
    assert _headers(ep) == [("Authorization", "basic")]


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


# --- main-walk DoS guard (deeply-nested string-splitting / nested-sink shapes) --- #
# Counterpart to the harvest-pass linearity guard: the MAIN extract() walk must not go
# quadratic on a single deeply-nested expression. A `.concat()`/`+` split chain (and a
# nested-sink chain) is the static-analysis-evasion obfuscation this product targets, and
# a crafted ~1-10 MB single expression stays under the ingest cap AND (over 1 MiB) skips
# beautify, so it reaches the raw walk. Pre-fix this walk was cleanly O(n^2) — the concat
# shape measured n=1000 0.74s, n=4000 15.7s, n=16000 282s (~4.3x per 2x) — stalling the
# single analyze worker for minutes-to-hours. Three independent quadratic sources were
# bounded, all the same class (an unbounded per-node full-text decode / O(depth) `.parent`
# on the tree walk): (1) a per-node `node.parent` probe in `_declared_names` (tree-sitter
# re-roots `.parent` from the top → O(depth) each; dominant for the concat/+ shapes),
# (2) an uncapped receiver decode per member call in `_handle_call`, and (3) an uncapped
# leaf decode in `_expr_token` (+ the `_endpoint` snippet) on the UNRESOLVED-sink render
# path, dominant for a nested `new WebSocket(new WebSocket(…))` shape. The three builders
# below pin all three: `+` isolates `_declared_names` (no call_expression); `.concat()`
# exercises the receiver decode; nested `WebSocket` exercises the `_expr_token`/snippet
# path (its outer args are unresolvable sinks).


def _concat_chain(depth: int) -> str:
    """``var u = "https://x/".concat("a").concat("a")…;`` — nested call_expressions."""
    return 'var u = "https://x/"' + '.concat("a")' * depth + ";"


def _plus_chain(depth: int) -> str:
    """``var u = "a" + "b" + "b" + …;`` — deeply left-nested binary_expressions, no calls."""
    return 'var u = "a"' + ' + "b"' * depth + ";"


def _nested_sink_chain(depth: int) -> str:
    """``x = new WebSocket(new WebSocket(…("/x")));`` — each outer arg is a non-static sink
    URL, routing through `_record_unresolved`→`_collapse_url`→`_expr_token`'s leaf decode."""
    return "x=" + "new WebSocket(" * depth + '"/x"' + ")" * depth + ";"


def _extract_seconds(source: str) -> float:
    start = time.perf_counter()
    extract(source)
    return time.perf_counter() - start


@pytest.mark.parametrize(
    "build_chain",
    [_concat_chain, _plus_chain, _nested_sink_chain],
    ids=["concat", "plus", "nested_sink"],
)
def test_extract_stays_linear_on_deep_split_chain_no_dos(build_chain):
    """extract() on a deeply-nested `.concat()`/`+`/nested-sink chain must stay linear;
    pre-fix the walk was O(n^2) (concat 282s at depth 16000, ~0.3s now).

    Three assertions, deliberately layered so the guard is both flake-proof and fast to
    fail on a real regression:
      * anchor ceiling at depth 4000 (~0.03-0.06s linear, ~50-100x headroom so runner
        jitter can't trip it) — a reintroduced O(n^2) is ~16s here and trips this FIRST,
        so CI fails in seconds instead of dragging the 282s depth-16000 case through;
      * a runner-speed-INDEPENDENT scaling ratio (4x the input is ~4x work when linear
        but ~16x when quadratic) — catches a partial regression an absolute bound alone
        would miss, and holds regardless of how fast the machine is;
      * an absolute ceiling at depth 16000 (the brief's wall-clock bound), only reached
        once the walk already looks linear."""
    extract('fetch("/warmup");')  # steady state: exclude one-time import/parse warmup
    anchor = _extract_seconds(build_chain(4000))
    assert anchor < 3.0, (
        f"extract() at depth 4000 took {anchor:.2f}s (linear ~0.05s; pre-fix O(n^2) 15.7s) — "
        f"DoS regression"
    )
    big = _extract_seconds(build_chain(16000))  # 4x the input
    assert big < anchor * 10, (  # linear ~4x, quadratic ~16x — 10x sits safely between
        f"extract() scaled {big / anchor:.1f}x for 4x deeper input "
        f"(anchor={anchor * 1000:.0f}ms, big={big * 1000:.0f}ms) — looks quadratic, DoS regression"
    )
    assert big < 5.0, (
        f"extract() at depth 16000 took {big:.2f}s (linear ~0.3s; pre-fix O(n^2) 282s) — "
        f"DoS regression"
    )


def test_oversized_node_text_is_skipped_dos_guard():
    """Mechanism guard for the shared span-cap `_text_if_short` (the bounded-decode
    primitive under all the sink-path fixes). A node whose byte span exceeds
    ``_MAX_NODE_TEXT_SPAN`` — a nested `.concat()` chain used as a call receiver — is not
    text-decoded: ``_text_if_short`` returns ``""``, which matches no dispatch branch, so
    the call is treated as unrecognized (never invented as a sink) while per-node work
    stays O(1). A short, real receiver still decodes so genuine sinks are untouched."""
    from recon.findings._jsast import _MAX_NODE_TEXT_SPAN, _text_if_short, _walk

    # `var u = (<huge concat chain>).get('/p');` — the outermost call is `().get('/p')`,
    # whose receiver object is the whole chain (well over the span cap). `_walk` is
    # pre-order, so the first call_expression it yields is that outermost call.
    src = 'var u = "https://x/"' + '.concat("a")' * 40 + ".get('/p');"
    root = _PARSER.parse(src.encode()).root_node
    outer_call = next(n for n in _walk(root) if n.type == "call_expression")
    receiver = outer_call.child_by_field_name("function").child_by_field_name("object")
    assert receiver.end_byte - receiver.start_byte > _MAX_NODE_TEXT_SPAN  # genuinely over cap
    assert _text_if_short(receiver) == ""  # -> skipped, not decoded

    # Control: a normal short receiver is decoded as before (cap does not change output).
    axios_root = _PARSER.parse(b"axios.get('/x');").root_node
    call = next(n for n in _walk(axios_root) if n.type == "call_expression")
    short_receiver = call.child_by_field_name("function").child_by_field_name("object")
    assert _text_if_short(short_receiver) == "axios"


def test_oversized_template_url_is_unresolvable_not_decoded_dos_guard():
    """Guard for the URL-span cap at its single decode source (`_string_value`). A
    `template_string` URL larger than ``_MAX_URL_SPAN`` — a crafted template that embeds
    inner sinks in ``${…}`` — resolves to None instead of being decoded, bounding the
    per-sink O(span) decode that was O(n^2) over a nested-template chain across EVERY sink
    type (fetch/axios/xhr/jquery). A normal in-cap template still decodes to its shape."""
    from recon.findings._jsast import _MAX_URL_SPAN, _string_value, _walk

    big = "`/api/" + "a" * (_MAX_URL_SPAN + 100) + "`"  # over-cap template literal
    root = _PARSER.parse(f"fetch({big});".encode()).root_node
    tmpl = next(n for n in _walk(root) if n.type == "template_string")
    assert tmpl.end_byte - tmpl.start_byte > _MAX_URL_SPAN  # genuinely over cap
    assert _string_value(tmpl) is None  # -> unresolvable, not decoded

    # Control: an in-cap template still decodes to its `${…}`-preserving shape.
    small = _PARSER.parse(b"fetch(`/api/users/${id}`);").root_node
    tmpl2 = next(n for n in _walk(small) if n.type == "template_string")
    assert _string_value(tmpl2) == "/api/users/${id}"


def test_deep_binary_and_destructuring_do_not_recurse_crash_dos_guard():
    """Crash-class guard: the extractor's two unbounded recursions are now iterative, so a
    crafted deep spine can't overflow the Python stack. `_leading_string` (an Authorization
    header value, a `"a"+"b"+…` chain) and `_declared_names.mark` (a `[[[…]]]` destructuring
    pattern) each recursed a crafted deep spine and raised RecursionError at a few KB;
    both must now complete, and the header scheme must still resolve from its leading literal."""
    depth = sys.getrecursionlimit() * 20  # far past the CPython recursion limit either way

    auth = 'axios.get("/x", {headers: {Authorization: "Bearer "' + '+"x"' * depth + "}});"
    result = extract(auth)  # must not raise RecursionError
    assert result.endpoints and result.endpoints[0].headers[0].scheme == "bearer"

    destructure = "const " + "[" * depth + "a" + "]" * depth + " = x;"
    extract(destructure)  # deep destructuring pattern must not raise RecursionError


def test_snippet_is_source_sliced_and_bounded_dos_guard():
    """Guard for the finding snippet's bounded source-slice (`_source_snippet`). The snippet
    must be sliced from the SOURCE bytes and capped at ``_SNIPPET_MAX_BYTES``, NOT built from
    ``node.text`` — tree-sitter's ``node.text`` materializes the whole node span (O(span)),
    which is O(n^2) summed over a nested-sink chain (each enclosing sink re-decoding an
    overlapping span). The slice stays byte-identical to the old ``node.text[:200]``."""
    from recon.findings._jsast import _SNIPPET_MAX_BYTES, _source_snippet

    src = b"fetch('/x/" + b"a" * 100_000 + b"');"  # a single huge call node
    end = len(src)
    snippet = _source_snippet(src, 0, end)
    assert snippet == src[0:end].decode()[:200]  # byte-identical to node.text[:200]
    assert len(snippet) == 200
    # Independent of where the node ENDS once past the cap — proof it reads ≤ cap bytes and
    # never materializes the full span (an over-cap `end` yields the same result as end=cap).
    assert _source_snippet(src, 0, end) == _source_snippet(src, 0, _SNIPPET_MAX_BYTES)


def test_nested_assignment_lhs_capped_and_baseurl_still_resolves():
    """Defect-9 guard: a left-nested member-target assignment (`((a.x=1).x=1)…`) has an LHS
    that CONTAINS every inner assignment, so decoding it per node (uncapped `_text`) re-decodes
    overlapping spans — O(n^2) in `collect_base_env`. The LHS is now capped via `_text_if_short`
    (it is only matched against the 23-char `axios.defaults.baseURL`). A real, short
    `axios.defaults.baseURL = …` in the same file must still resolve — the cap changes no real
    output."""
    src = "(" * 200 + "a.x=1" + ").x=1" * 200 + "; axios.defaults.baseURL='/api';"
    env = collect_base_env(_PARSER.parse(src.encode()).root_node, src.encode())
    assert env.default_base == "/api"
