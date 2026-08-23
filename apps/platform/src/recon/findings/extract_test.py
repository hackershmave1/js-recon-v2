"""Colocated tests for the in-process JS network-call extractor (Vespasian).

Pure unit tests — parse JS strings, assert the reconstructed calls. No infra.
"""

from __future__ import annotations

import statistics
import sys
import time
from collections.abc import Callable

import pytest
from tree_sitter import Node

from recon.findings.extract import _PARSER, RawEndpoint, collect_base_env, extract


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


# --- cross-module / cross-chunk const resolution (P1/P2) ---------------------- #
# A URL built from consts IMPORTED from another chunk is unresolvable per-file today
# and counts as `unattributed` (bs-crosschunk). Given the resolved import values
# (recon.findings._modulegraph supplies them from a run-level export index) the sink
# folds them to a full URL. The fold consults ONLY cross-module consts + literals,
# never local consts, so the honesty calibration is preserved.

_ORDERS = {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}


def test_cross_module_concat_resolves_to_full_url():
    r = extract("fetch(API_BASE + ORDERS_PATH)", cross_module_consts=_ORDERS)
    assert r.unattributed == 0 and r.unresolved == []
    assert [(e.method, e.url) for e in r.endpoints] == [
        ("GET", "https://api.acme.com/api/v3/orders")
    ]


def test_cross_module_bare_const_resolves():
    r = extract("fetch(API_BASE)", cross_module_consts={"API_BASE": "https://api.acme.com/x"})
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "https://api.acme.com/x")]
    assert r.unattributed == 0


def test_local_const_folds_even_with_cross_module_index_in_scope():
    # Phase 2 (Option B) REVERSES the cross-chunk slice's local-const exclusion. `resource` is a
    # single-unshadowed local const, so `"/api/v1/" + resource` folds to a certain value — the
    # poison set makes it 0-FP (see `test_local_const_operand_resolves_bs_concat` for the
    # no-index case). An unrelated cross-module index in scope does not change that.
    r = extract(
        'const resource = "widgets"; fetch("/api/v1/" + resource)', cross_module_consts=_ORDERS
    )
    assert r.unattributed == 0
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "/api/v1/widgets")]


def test_cross_module_plus_unresolvable_operand_is_not_guessed():
    # all-or-none STILL holds for a GENUINELY unresolvable operand (finding 2): `p` is undeclared
    # (not a local const, not cross-module), so the whole chain stays honestly unattributed
    # rather than emitting a half-guessed URL.
    r = extract("fetch(API_BASE + p)", cross_module_consts={"API_BASE": "https://api.acme.com"})
    assert r.endpoints == [] and r.unattributed == 1


def test_cross_module_absent_scope_is_unchanged():
    # No index in scope -> byte-for-byte today's behavior (backward compatible).
    r = extract("fetch(API_BASE + ORDERS_PATH)")
    assert r.endpoints == [] and r.unattributed == 1


def test_cross_module_deep_chain_capped_not_recursion_error():
    # Deep +-chain WITH a cross-module index in scope must still degrade via the span/
    # depth cap, never blow the stack.
    src = "fetch(API_BASE" + "".join(['+"x"'] * 2000) + ")"
    r = extract(src, cross_module_consts={"API_BASE": "https://api.acme.com"})
    assert len(r.unresolved) == 1 and r.endpoints == []


# --- webpack require-alias member resolution (2b) ----------------------------- #
# `var r = __webpack_require__(389); fetch(r.t + r.M)` — the sink operands are member
# expressions on a require-alias, resolved via env.webpack_members[alias][export].

_WM = {"r": {"t": "https://api.acme.com", "M": "/api/v3/orders"}}


def test_webpack_member_concat_resolves():
    r = extract("fetch(r.t + r.M)", webpack_members=_WM)
    assert r.unattributed == 0
    assert [(e.method, e.url) for e in r.endpoints] == [
        ("GET", "https://api.acme.com/api/v3/orders")
    ]


def test_webpack_bare_member_resolves():
    r = extract("fetch(r.t)", webpack_members={"r": {"t": "https://api.acme.com/x"}})
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "https://api.acme.com/x")]


def test_webpack_member_on_non_alias_is_unattributed():
    # receiver not a require-alias -> never fold (honesty)
    r = extract("fetch(x.y)", webpack_members=_WM)
    assert r.endpoints == [] and r.unattributed == 1


def test_webpack_member_unknown_export_is_unattributed():
    r = extract("fetch(r.NOPE)", webpack_members=_WM)
    assert r.endpoints == [] and r.unattributed == 1


def test_webpack_member_plus_unresolvable_operand_is_not_guessed():
    # `p` is undeclared -> the `r.t + p` chain stays unattributed (all-or-none honesty). A LOCAL
    # const operand WOULD now fold (Phase 2) — see `test_local_const_base_plus_path_resolves`.
    r = extract("fetch(r.t + p)", webpack_members=_WM)
    assert r.endpoints == [] and r.unattributed == 1


def test_webpack_deep_member_chain_is_unattributed():
    # only a simple `alias.export` folds; `r.t.deep` (nested member) stays unattributed
    r = extract("fetch(r.t.deep)", webpack_members=_WM)
    assert r.endpoints == [] and r.unattributed == 1


def test_webpack_absent_members_unchanged():
    r = extract("fetch(r.t + r.M)")
    assert r.endpoints == [] and r.unattributed == 1


# --- Phase 2: single-unshadowed LOCAL const propagation (Option B) ------------ #
# `_declared_names` poisons any name bound/shadowed/reassigned more than once anywhere in the
# file, so `const_prefixes` holds only names bound EXACTLY once — folding one is CERTAIN, not a
# guess, so it is 0-FP (the value cannot differ at the sink). Phase 2 resolves such a local const
# at a BARE sink argument AND as a `+`/concat operand, reversing the cross-chunk slice's
# deliberate local-const exclusion (a scoped choice, not a safety one). Ambiguous bindings stay
# `unattributed` — honesty (REQ-C2) is preserved by the poison set, not by refusing to resolve.


def test_local_const_bare_url_resolves():
    r = extract('const u = "https://api.acme.com/orders"; fetch(u)')
    assert r.unattributed == 0 and r.unresolved == []
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "https://api.acme.com/orders")]


def test_local_const_operand_resolves_bs_concat():
    # recon-range `bs-concat`: a local literal const in a `+` chain now folds (Option B). The
    # cross-chunk slice deliberately left this `unattributed`; the poison set makes folding safe.
    r = extract('const resource = "widgets"; fetch("/api/v1/" + resource)')
    assert r.unattributed == 0 and r.unresolved == []
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "/api/v1/widgets")]


def test_local_const_base_plus_path_resolves():
    r = extract('const BASE = "https://api.acme.com"; fetch(BASE + "/api/v3/orders")')
    assert [(e.method, e.url) for e in r.endpoints] == [
        ("GET", "https://api.acme.com/api/v3/orders")
    ]


def test_local_const_axios_member_resolves():
    r = extract('const u = "https://api.acme.com/x"; axios.get(u)')
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "https://api.acme.com/x")]


def test_local_const_and_cross_module_operand_both_resolve():
    # a local const + a cross-module const in one chain — both lanes fold now (Option B).
    r = extract(
        'const p = "/x"; fetch(API_BASE + p)',
        cross_module_consts={"API_BASE": "https://api.acme.com"},
    )
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "https://api.acme.com/x")]


# --- Phase 2 shape (b): expression-valued bindings propagate (recon.findings._dataflow) -----
# A URL BUILT from local consts/literals and held in a local const, then fetched: the binding's
# VALUE is a `+` chain, not a bare literal, so it is resolved once by `collect_local_consts`
# (memoized, cycle- + depth-capped) and lands in `const_prefixes` like any other local const.


def test_expression_binding_resolves_base_plus_path():
    r = extract('const base = "https://api.acme.com"; const url = base + "/v3/orders"; fetch(url)')
    assert r.unattributed == 0
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "https://api.acme.com/v3/orders")]


def test_expression_binding_of_two_literals_resolves():
    r = extract('const u = "https://api.acme.com" + "/x"; axios.get(u)')
    assert [(e.method, e.url) for e in r.endpoints] == [("GET", "https://api.acme.com/x")]


def test_expression_binding_with_unresolvable_operand_stays_unattributed():
    # the built value references a CALL result -> unresolvable operand -> honest miss (all-or-none).
    r = extract('const url = base() + "/x"; fetch(url)')
    assert r.endpoints == [] and r.unattributed == 1


def test_expression_binding_reference_cycle_is_safe():
    # `const a = b + "/x"; const b = a + "/y"` -> a reference cycle: both stay unresolved (the
    # `active` set breaks it), no hang, no guess.
    r = extract('const a = b + "/x"; const b = a + "/y"; fetch(a)')
    assert r.endpoints == [] and r.unattributed == 1


# --- Phase 2 honesty: ambiguous bindings stay unattributed (0-FP) ------------- #


def test_reassigned_local_is_poisoned_not_resolved():
    # `let u` reassigned -> `_declared_names` count 2 -> poisoned -> never folded (could be either).
    r = extract('let u = "/a"; u = "/b"; fetch(u)')
    assert r.endpoints == [] and r.unattributed == 1


def test_param_shadowed_local_is_poisoned_not_resolved():
    # the outer const is shadowed by a same-name arrow param -> poisoned -> honest miss.
    r = extract('const r = "/a"; [1].forEach((r) => fetch("/api/" + r))')
    assert r.endpoints == [] and r.unattributed == 1


def test_non_literal_local_binding_stays_unattributed_bs_variable():
    # recon-range `bs-variable`: `const u = pickUrl(); fetch(u)` — the value is a CALL, not a
    # string literal, so `u` never enters `const_prefixes`; honest miss, never a guessed URL.
    r = extract('function pickUrl(){return "/api/v1/dynamic"} const u = pickUrl(); fetch(u)')
    assert r.endpoints == [] and r.unattributed == 1


def test_undeclared_bare_var_stays_unattributed():
    r = extract("fetch(u)")  # `u` is never declared -> not a local const -> unattributed
    assert r.endpoints == [] and r.unattributed == 1


# --- Phase 2 honesty: IN-PLACE mutation poisons the const (§4 gate-2 CONFIRMED 0-FP fix) ------ #
# A name bound once but MUTATED (`+=`, `||=`, `++`, a `for (x of …)` loop target) has a value at
# the sink that differs from its declared literal. `_declared_names` must poison these or the fold
# emits the STALE value as an attributed endpoint — a false positive on ordinary string-building
# code. (The higher-model review caught this: the poison walk had omitted the mutation node types.)


def test_compound_assignment_poisons_local_not_resolved():
    r = extract('let path = "/api/v1"; path += "/users"; fetch(path)')
    assert r.endpoints == [] and r.unattributed == 1  # would fold to the stale `/api/v1` pre-fix


def test_compound_assignment_operand_poisons_local_not_resolved():
    r = extract('let base = "https://api.acme.com"; base += "/v2"; fetch(base + "/orders")')
    assert r.endpoints == [] and r.unattributed == 1


def test_logical_assignment_poisons_local_not_resolved():
    r = extract('let u = ""; u ||= "/real/admin"; fetch(u)')
    assert r.endpoints == [] and r.unattributed == 1


def test_update_expression_poisons_local_not_resolved():
    r = extract('let p = "/a"; p++; fetch(p)')
    assert r.endpoints == [] and r.unattributed == 1


def test_for_of_reassigned_local_poisons_not_resolved():
    # `for (url of arr)` reassigns the bare outer `url` each pass -> poisoned -> honest miss.
    r = extract('let url = "/a"; for (url of arr) fetch(url)')
    assert r.endpoints == [] and r.unattributed == 1


def test_parenthesized_assignment_target_poisons_local():
    # §4 gate-2 CONFIRMED 0-FP: `(x) = v` / `(x) += v` is a legal assignment target that wraps the
    # name in a `parenthesized_expression`; the poison walk must descend it or the STALE const
    # folds at the sink. Nested parens `((x)) = v` too.
    r = extract('let u = "/a"; (u) = "/b"; fetch(u)')
    assert r.endpoints == [] and r.unattributed == 1
    assert extract('let u = "/a"; (u) += "/b"; fetch(u + "/x")').endpoints == []
    assert extract('let u = "/a"; ((u)) = "/b"; fetch(u)').endpoints == []


def test_with_statement_drops_all_local_consts():
    # §4 gate-2 LOW: `with (obj)` rebinds free names inside against obj at runtime, so a local
    # const could differ at the sink. Poison the whole file (`with` is a SyntaxError in ESM/strict
    # -> ~0 real recall lost) -> honest miss, never the stale value.
    r = extract('const api = "/stale"; with (cfg) { fetch(api); }')
    assert r.endpoints == [] and r.unattributed == 1


# --- S1: const-resolution extends to XHR / jQuery / WebSocket sinks ------------ #
# `fetch`/`axios` already fold a single-unshadowed local const at the URL arg (Phase 2), but
# `XMLHttpRequest.open`, jQuery, and `new WebSocket` accepted a bare string LITERAL only — so the
# same `const u = "…"; sink(u)` resolved for a fetch yet landed `unattributed` for these three.
# They now route their URL arg through the same `_resolve_url`; the poison set carries the 0-FP
# guarantee across unchanged (a reassigned/param URL still never folds a guess).


def test_xhr_open_resolves_local_const():
    r = extract('const u = "/api/v1/items"; xhr.open("GET", u)')
    assert len(r.endpoints) == 1 and r.unresolved == [] and r.unattributed == 0
    ep = r.endpoints[0]
    assert (ep.kind, ep.method, ep.url) == ("xhr", "GET", "/api/v1/items")


def test_xhr_open_resolves_const_base_plus_path():
    r = extract('const b = "/api/v1"; xhr.open("POST", b + "/orders")')
    assert len(r.endpoints) == 1 and r.unattributed == 0
    assert (r.endpoints[0].method, r.endpoints[0].url) == ("POST", "/api/v1/orders")


def test_xhr_open_reassigned_local_still_unattributed():
    # 0-FP carries over: a reassigned (poisoned) name must NOT fold its stale literal at `.open`.
    r = extract('let u = "/a"; u = "/b"; xhr.open("GET", u)')
    assert r.endpoints == [] and r.unattributed == 1
    assert r.unresolved[0].kind == "xhr"


def test_jquery_get_resolves_local_const():
    r = extract('const u = "/api/items"; $.get(u)')
    assert len(r.endpoints) == 1 and r.unattributed == 0
    assert (r.endpoints[0].kind, r.endpoints[0].method, r.endpoints[0].url) == (
        "jquery",
        "GET",
        "/api/items",
    )


def test_jquery_ajax_resolves_local_const_url():
    r = extract('const u = "/api/y"; $.ajax({url: u, type: "PUT"})')
    assert len(r.endpoints) == 1 and r.unattributed == 0
    assert (r.endpoints[0].method, r.endpoints[0].url) == ("PUT", "/api/y")


def test_jquery_post_resolves_local_const():
    r = extract('const u = "/api/login"; $.post(u, {a: 1})')
    assert len(r.endpoints) == 1 and r.unattributed == 0
    assert (r.endpoints[0].method, r.endpoints[0].url) == ("POST", "/api/login")


def test_websocket_resolves_local_const():
    r = extract('const u = "wss://h.example.com/socket"; new WebSocket(u)')
    assert len(r.endpoints) == 1 and r.unattributed == 0
    assert (r.endpoints[0].kind, r.endpoints[0].method, r.endpoints[0].url) == (
        "websocket",
        "WSS",
        "wss://h.example.com/socket",
    )


def test_param_url_stays_unattributed_the_static_ceiling():
    # A URL that arrives as a FUNCTION PARAMETER is not a local const (no single static value
    # here) -> it stays honestly `unattributed`. Resolving it would need interprocedural data-flow,
    # deliberately out of scope (the §4-forbidden taint zone; tracked as measure-first debt D30).
    r = extract('function load(a) { xhr.open("GET", a); }')
    assert r.endpoints == [] and r.unattributed == 1


def test_websocket_reassigned_local_still_unattributed():
    # 0-FP symmetry with XHR (§4 review finding 6): the poison set must hold at EVERY new sink, so
    # a reassigned name never folds its stale value at `new WebSocket` either.
    r = extract('let u = "wss://a/x"; u = "wss://b/y"; new WebSocket(u)')
    assert r.endpoints == [] and r.unattributed == 1
    assert r.unresolved[0].kind == "websocket"


def test_jquery_param_shadowed_local_still_unattributed():
    # `u` is bound as a const AND as a parameter of `f` -> bound twice -> poisoned -> the $.get URL
    # stays unattributed (0-FP symmetry guard at the jQuery sink).
    r = extract('const u = "/a"; function f(u) { return u; } $.get(u)')
    assert r.endpoints == [] and r.unattributed == 1
    assert r.unresolved[0].kind == "jquery"


def test_websocket_leading_template_const_resolves():
    # The leading-`${CONST}` fold reaches the new sinks too, not just bare-id / `+`-concat forms.
    r = extract('const API = "wss://h.example.com"; new WebSocket(`${API}/ws`)')
    assert len(r.endpoints) == 1 and r.unattributed == 0
    assert (r.endpoints[0].method, r.endpoints[0].url) == ("WSS", "wss://h.example.com/ws")


def test_websocket_protocol_relative_const_not_mutated():
    # base="" makes `_join_base` the identity: a protocol-relative URL folds to ITSELF, with no
    # slash rewrite -- guards the reroute through `_resolve_url` against a base-join mutation.
    r = extract('const u = "//h.example.com/socket"; new WebSocket(u)')
    assert len(r.endpoints) == 1
    assert (r.endpoints[0].method, r.endpoints[0].url) == ("WS", "//h.example.com/socket")


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


def _time_extract(source: str) -> float:
    """Wall-clock for a single ``extract(source)`` run."""
    start = time.perf_counter()
    extract(source)
    return time.perf_counter() - start


def _min_extract_seconds(build_chain: Callable[[int], str], depth: int, reps: int) -> float:
    """Fastest (min) wall-clock over ``reps`` extract() runs at one depth. The min is the
    least-perturbed sample (extract() is deterministic), so it is the noise-robust value for
    the cheap-depth fast-fail ceiling below."""
    return min(_time_extract(build_chain(depth)) for _ in range(reps))


def _interleaved_median_ratio(
    build_chain: Callable[[int], str], anchor_depth: int, big_depth: int, reps: int = 3
) -> tuple[float, float]:
    """Interleave the anchor and big depths (a, b, a, b, …) over ``reps`` rounds and return
    ``(min_big, median_ratio)``: the fastest big (for the absolute depth-16000 ceiling in the
    test) and the MEDIAN of the per-round ``big / anchor`` quotients (each from ONE round, both
    depths timed back-to-back under the same conditions).

    Interleaving + a per-round median is the de-flake. The prior probe timed every anchor round
    BEFORE every big round and divided the two mins, so a shared-CI contention burst that began
    during the (later) big phase inflated ``big`` while the already-recorded anchors stayed
    clean — a phase mismatch that spiked the ratio on purely linear code (both recorded flakes:
    a clean ~56ms anchor vs a contended ~700ms big → ~10-13x, over the 12x line). A per-depth
    min could not undo it: the burst covered the whole contiguous big phase, so every big
    sample was slow. Interleaving exposes the two depths to the SAME contention windows, so a
    per-round ratio cancels contention that spans a whole round (a burst slowing a round's big
    also slows its adjacent anchor). The only rounds a single burst can still distort are the two
    whose anchor/big straddle an EDGE — one HIGH (anchor clean, big slowed) at the rising edge,
    one LOW (anchor slowed, big clean) at the falling edge — and since those land on OPPOSITE
    sides, the median of 3 lands on the clean round between them. (``reps`` is odd; bump it to 5
    only if two *overlapping* bursts ever recur — untested, never observed.) A real O(n^2) is
    slow in EVERY round, so the median still trips the guard. (A swept-burst simulation puts the
    linear-flake rate at 0% here across all burst positions, vs ~3% for the old min-of-mins,
    while never masking a 16x quadratic — see the commit.)"""
    min_big = float("inf")
    ratios: list[float] = []
    for _ in range(reps):
        anchor = _time_extract(build_chain(anchor_depth))
        big = _time_extract(build_chain(big_depth))
        min_big = min(min_big, big)
        ratios.append(big / anchor)  # same round: both depths timed under the same conditions
    return min_big, statistics.median(ratios)


@pytest.mark.dos_timing  # wall-clock scaling guard: host-lane-only (see pyproject / DEBT D21)
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
      * a fast-fail anchor ceiling at depth 4000 (~0.05-0.25s linear, ~15-40x headroom under
        the 3.0s ceiling so runner jitter can't trip it), checked BEFORE the expensive depth is
        ever run — a reintroduced O(n^2) is ~16s here and trips this FIRST, well under a minute,
        instead of dragging the 282s depth-16000 case through;
      * a scaling ratio with generous headroom (when linear, 4x the input is ~3-4x the work;
        ~16x when quadratic), measured by interleaving the two depths and taking the per-round
        MEDIAN so shared-CI contention cancels (see `_interleaved_median_ratio` — this is the
        de-flake; the prior all-anchors-then-all-bigs order divided two mins from different
        phases, letting a burst in the big phase spike the ratio to ~10-13x on linear code).
        NOTE: a 4x step vs a 12x line resolves scaling exponents above ~1.8; a milder
        super-linear residual that stays under the ceilings is the absolute ceilings' job to
        catch, not the ratio's (widening the depth gap to sharpen the ratio would re-introduce
        the flake — the linear ratio itself rises toward the line);
      * an absolute ceiling at depth 16000 (the brief's wall-clock bound), only reached once
        the walk already looks linear."""
    extract('fetch("/warmup");')  # steady state: exclude one-time import/parse warmup
    # 1) Fast-fail a gross O(n^2) on the CHEAP anchor depth alone, BEFORE running the expensive
    #    depth-16000 case: min-of-3 is noise-robust and the ceiling's ~15-40x headroom means a
    #    linear spike can't trip it, while a reintroduced O(n^2) (~16s) fails here in seconds.
    anchor = _min_extract_seconds(build_chain, 4000, reps=3)
    assert anchor < 3.0, (
        f"extract() at depth 4000 took {anchor:.2f}s (linear ~0.05s; pre-fix O(n^2) 15.7s) — "
        f"DoS regression"
    )
    # 2) Depth 4000 is fast -> measure the scaling ratio robustly (interleaved per-round median).
    big, ratio = _interleaved_median_ratio(build_chain, 4000, 16000)  # big = 4x the input
    assert ratio < 12, (  # interleaved per-round median: linear ~3-4x, quadratic ~16x
        f"extract() median-scaled {ratio:.1f}x for 4x deeper input "
        f"(depth-4000 {anchor * 1000:.0f}ms, fastest depth-16000 {big * 1000:.0f}ms) — "
        f"looks quadratic, DoS regression"
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


# --- Phase 2: page routes (href/src/action, nav sinks, off-sink harvest) ------
# A distinct category from the API lanes — a client-side navigation target, not a backend
# call. `_looks_like_route` is the false-positive gate; sink/href context classifies API vs
# route, string shape is only a tiebreak for a context-free harvested literal.

from recon.findings._jsast import _looks_like_route  # noqa: E402


def test_looks_like_route_accepts_real_routes():
    for skeleton in (
        "/player/:id",
        "/player/${id}",
        "https://app.acme.io/player/1",
        "/about",
        "${base}/home",
        "/user/:id/report/:id",
    ):
        assert _looks_like_route(skeleton), skeleton


def test_looks_like_route_rejects_non_routes():
    # no anchor (bare word, dotted chain, MIME type), a static asset, or a pure placeholder.
    for skeleton in (
        "a.b.c",
        "image/png",
        "text/plain",
        "/static/logo.png",
        "/vendor.js.map",
        "/bundle.js",
        "/logo.svg",
        "EXPR",
        "",
        "userId",
        "/has space",
    ):
        assert not _looks_like_route(skeleton), skeleton


def test_href_concat_is_a_low_confidence_page_route():
    # user example 2: href built via .concat() off window.location.origin -> /player/:id route.
    r = extract('var link = {href:"".concat(window.location.origin, "/player/").concat(id)};')
    assert len(r.routes) == 1 and r.endpoints == []
    route = r.routes[0]
    assert (route.kind, route.method, route.url, route.confidence) == (
        "route",
        "",
        "/player/:id",
        "low",
    )


def test_src_and_action_keys_are_routes_href_assets_are_not():
    assert extract('var f = {action:"/submit/order"};').routes[0].url == "/submit/order"
    assert extract('var i = {src:"/embed/player/1"};').routes[0].url == "/embed/player/1"
    # a Redux `action:"USER_LOGIN"` has no path anchor -> the FP gate drops it (no route).
    assert extract('var a = {action:"USER_LOGIN"};').routes == []


def test_href_pseudo_schemes_and_assets_rejected():
    for value in ('"#top"', '"mailto:a@b.com"', '"javascript:void(0)"', '"/logo.png"'):
        assert extract("var x = {href:" + value + "};").routes == [], value


def test_nav_sink_confidence_high_only_for_explicit_global_receiver():
    # an explicit global receiver (window./document.-anchored) -> high confidence.
    for src in ('window.location.assign("/dashboard")', 'document.location.replace("/dashboard")'):
        r = extract(src)
        assert len(r.routes) == 1
        assert (r.routes[0].method, r.routes[0].url, r.routes[0].confidence) == (
            "",
            "/dashboard",
            "high",
        )
    # a BARE receiver could be a shadowing local -> same route, but LOW confidence (§4 review).
    bare = extract('location.assign("/dashboard")')
    assert (bare.routes[0].url, bare.routes[0].confidence) == ("/dashboard", "low")


def test_string_replace_on_var_named_location_is_low_not_a_high_confidence_phantom():
    # String.prototype.replace on a var named `location` is text-matched as a nav sink, but a
    # bare receiver is LOW confidence — never a HIGH-confidence phantom route (§4 review MEDIUM).
    r = extract('var location = slug; location.replace("/api/user", "/v2/api/user");')
    assert all(x.confidence == "low" for x in r.routes)


def test_window_open_is_a_route_not_an_xhr():
    # window.open is checked BEFORE the `.open`->XHR branch, so it becomes a route, not a sink.
    r = extract('window.open("/help/getting-started")')
    assert r.endpoints == [] and len(r.routes) == 1
    assert r.routes[0].url == "/help/getting-started"


def test_history_pushstate_reads_url_from_third_arg():
    r = extract('history.pushState({page:2}, "", "/feed/2")')
    assert len(r.routes) == 1 and r.routes[0].url == "/feed/2"


def test_router_push_string_and_object_forms():
    assert extract('router.push("/settings/profile")').routes[0].url == "/settings/profile"
    assert extract('router.push({pathname:"/account/:id"})').routes[0].url == "/account/:id"


def test_non_http_open_is_still_ignored_and_yields_no_route():
    # regression: modal.open("settings","/foo") is neither window.open nor an XHR method.
    r = extract('modal.open("settings", "/foo")')
    assert r.endpoints == [] and r.routes == []


def test_off_sink_absolute_concat_url_is_harvested_low_confidence():
    # user example 1: a `.concat()`-built https:// URL that is RETURNED, never passed to a sink.
    src = 'function u(t,e){return "https://".concat(window.location.host).concat(t,"/player/").concat(e)}'
    r = extract(src)
    assert len(r.routes) == 1 and r.routes[0].confidence == "low"
    assert r.routes[0].url.startswith("https://") and "/player/" in r.routes[0].url


def test_off_sink_api_shaped_absolute_url_goes_to_generic_lane():
    # a context-free absolute URL whose SHAPE reads API-ish rides the suspected-API lane.
    r = extract('var base = "https://api.acme.io/v1/users";')
    assert r.routes == []
    assert any(g.url == "https://api.acme.io/v1/users" for g in r.generic)


def test_absolute_url_at_a_sink_is_not_double_harvested():
    # the top-level-expression guard: a sink's own URL arg is claimed, never re-emitted.
    r = extract('fetch("https://api.acme.io/v1/users")')
    assert len(r.endpoints) == 1 and r.routes == [] and r.generic == []


def test_relative_off_sink_literal_is_not_harvested():
    # harvesting is absolute-only: a bare "/path" literal off-sink needs a sink/href to anchor.
    assert extract('var p = "/just/a/string/path";').routes == []


def test_namespace_urls_are_not_harvested_as_routes():
    # SVG/XML xmlns and schema.org pervade bundles but are never navigable pages.
    assert extract('var ns = "http://www.w3.org/2000/svg";').routes == []
    assert extract('var s = "https://schema.org/Person";').routes == []


def test_nested_concat_harvest_emits_only_the_top_level_expression():
    # the outer concat is harvested and its span claimed; the inner concats do not re-emit.
    r = extract('var u = "https://cdn.acme.io/".concat("live/").concat(streamId);')
    assert len(r.routes) == 1


@pytest.mark.dos_timing  # wall-clock scaling guard: host-lane-only (see pyproject / DEBT D21)
def test_harvest_routes_pass_stays_linear_no_dos():
    # DoS-regression guard (§4 review HIGH) for the harvest pass specifically: on a deeply
    # nested .concat() chain — the string-splitting obfuscation this product targets — it must
    # be O(n), not O(n^2). An earlier fix walked node.parent per node (O(depth) in tree-sitter)
    # and took ~66s at this size; the span-cap-first / claimed-range version is well under 1s.
    # Tested in ISOLATION on purpose: the main extract() walk has its own separate, PRE-EXISTING
    # O(n^2) on such chains (a per-node receiver decode — tracked as debt) that would otherwise
    # mask this guard.
    import time

    from recon.findings._jsast import Extraction
    from recon.findings.extract import _harvest_routes

    tree = _PARSER.parse(('"https://x/"' + '.concat("a")' * 20000).encode())
    result = Extraction()
    start = time.perf_counter()
    _harvest_routes(tree.root_node, result)
    assert time.perf_counter() - start < 10.0
    assert len(result.routes) <= 1


def _flat_sinks(n: int) -> str:
    """``fetch("/api/u0");fetch("/api/u1");…`` — n independent recorded sinks, so the harvest
    pass's ``claimed`` list grows to O(n). This is the many-CLAIMED dimension, ORTHOGONAL to
    the deep-single-chain shape the guard above covers (there the result stays empty, so
    ``claimed`` never grows and the per-node containment scan is O(1))."""
    return ";".join(f'fetch("/api/u{i}")' for i in range(n))


def _harvest_case(n: int) -> tuple[Node, list[RawEndpoint]]:
    """Untimed setup for the harvest DoS probe: parse n flat sinks and run the main call pass so
    the harvest ``claimed`` list is populated to O(n). Returns ``(root_node, recorded_endpoints)``
    so the isolated ``_harvest_routes`` sweep can be timed repeatedly without re-parsing."""
    from recon.findings._base_env import collect_base_env
    from recon.findings._jsast import Extraction, _walk
    from recon.findings.extract import _handle_call

    data = _flat_sinks(n).encode()
    tree = _PARSER.parse(data)
    env = collect_base_env(tree.root_node, data)
    result = Extraction()
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            _handle_call(node, result, env, frozenset())
    return tree.root_node, result.endpoints


def _time_harvest(root: Node, endpoints: list[RawEndpoint]) -> float:
    """Wall-clock for ONE isolated ``_harvest_routes`` over a fresh result seeded with the
    recorded (claimed) ``endpoints`` — measures only the claimed-containment sweep, never
    mutating the shared ``endpoints`` list (slice-assigned into a fresh Extraction each call)."""
    from recon.findings._jsast import Extraction
    from recon.findings.extract import _harvest_routes

    fresh = Extraction()
    fresh.endpoints[:] = endpoints
    start = time.perf_counter()
    _harvest_routes(root, fresh)
    return time.perf_counter() - start


def _interleaved_harvest_ratio(
    anchor_n: int, big_n: int, reps: int = 3
) -> tuple[float, float, float, int]:
    """Harvest counterpart to ``_interleaved_median_ratio``: build both cases once (untimed), then
    interleave the anchor/big ``_harvest_routes`` timings (a, b, a, b, …) over ``reps`` rounds and
    return ``(min_anchor, min_big, median_ratio, n_anchor)``.

    Interleaving + a per-round median is the de-flake — see ``_interleaved_median_ratio`` for the
    full argument. The prior probe (the removed ``_harvest_seconds``) took each size's internal
    min-of-3 in two contiguous phases (every anchor rep before every big rep), so a shared-CI
    contention burst landing in the big phase inflated only ``big`` and spiked the ratio on purely
    linear code (~14-19x observed under the Docker integration lane's memory pressure). That is the
    exact phase mismatch PR #86 fixed for the deep-split guard but never applied here. Timing both
    sizes back-to-back each round exposes them to the same contention window, so a per-round ratio
    cancels a burst spanning a round and the median lands on a clean round. This guard now runs
    host-lane-only (``@pytest.mark.dos_timing``); the interleave keeps its native-runner home
    robust so a real O(n^2) still trips it (slow in every round → the median trips too)."""
    anchor_root, anchor_eps = _harvest_case(anchor_n)
    big_root, big_eps = _harvest_case(big_n)
    min_anchor = float("inf")
    min_big = float("inf")
    ratios: list[float] = []
    for _ in range(reps):
        anchor = _time_harvest(anchor_root, anchor_eps)
        big = _time_harvest(big_root, big_eps)
        min_anchor = min(min_anchor, anchor)
        min_big = min(min_big, big)
        ratios.append(big / max(anchor, 1e-6))  # same round: both timed under the same conditions
    return min_anchor, min_big, statistics.median(ratios), len(anchor_eps)


@pytest.mark.dos_timing  # wall-clock scaling guard: host-lane-only (see pyproject / DEBT D21)
def test_harvest_stays_linear_on_many_flat_sinks_no_dos():
    """DoS guard for the ORTHOGONAL many-claimed dimension. With n recorded sinks the harvest
    pass's ``claimed`` list is O(n), and the pre-fix per-node ``any(... in claimed)`` containment
    scan made the whole pass O(n^2) — ~34s at 20k flat sinks, a <1MB crafted input well under the
    10 MB ingest cap: a worker-stalling DoS the deep-single-chain guard above cannot catch (there
    ``claimed`` stays empty). The merged-interval single-pointer sweep is O(n log n): a 4x input
    scales ~linearly, not ~16x. Absolute ceilings are the robust primary guard; the ratio is a
    secondary tripwire (pre-fix ~14x, post-fix ~4x), measured by interleaving the two sizes and
    taking the per-round MEDIAN so shared-CI contention cancels (see ``_interleaved_harvest_ratio``
    — the de-flake PR #86 gave the deep-split guard but this one originally lacked)."""
    extract('fetch("/warmup")')  # exclude one-time import/parse warmup
    anchor, big, ratio, n_anchor = _interleaved_harvest_ratio(2000, 8000)  # big = 4x the input
    assert n_anchor == 2000  # sanity: the sinks ARE recorded, so `claimed` really is O(n)
    assert anchor < 2.0, f"harvest at 2000 flat sinks took {anchor:.2f}s (linear ~20ms) — DoS"
    assert big < 3.0, (
        f"harvest at 8000 flat sinks took {big:.2f}s (linear ~80ms; pre-fix ~5s) — DoS"
    )
    assert ratio < 10, (
        f"harvest median-scaled {ratio:.1f}x for 4x more sinks "
        f"(2000: {anchor * 1000:.0f}ms, 8000: {big * 1000:.0f}ms) — looks quadratic, DoS regression"
    )


def test_node_budget_curtails_pathological_tree_dos_guard(monkeypatch):
    """One-shot AST work budget (DEBT D21): a tree over ``_MAX_AST_NODES`` bounds the two
    EXPENSIVE passes (the sink walk + harvest) to a prefix, so worst-case wall-clock stays under
    the analyze heartbeat regardless of input shape. A tree UNDER the cap is untouched (full
    recall). The base-env / poison pre-pass is never bounded, so curtailment can never turn an
    honest miss into a wrong resolution (soundness)."""
    from recon.findings import extract as extract_mod

    src = _flat_sinks(400)  # ~4400 nodes; fast to build/parse, well over the tiny cap below
    assert len(extract(src).endpoints) == 400  # control: the default (huge) cap finds every sink
    monkeypatch.setattr(extract_mod, "_MAX_AST_NODES", 500)  # tiny cap -> bound to a prefix
    curtailed = extract(src)
    assert 0 < len(curtailed.endpoints) < 400  # partial + honest — bounded work, never a crash


def test_walk_limit_caps_node_count():
    """The one-shot budget primitive: `_walk(limit)` yields at most `limit` nodes; `None` is
    unbounded (every non-budget caller); `<= 0` yields nothing (the unreachable-in-prod edge)."""
    from recon.findings._jsast import _walk

    root = _PARSER.parse(b"a;b;c;d;e;").root_node
    total = sum(1 for _ in _walk(root))
    assert total > 3
    assert sum(1 for _ in _walk(root, None)) == total  # None -> unbounded
    assert sum(1 for _ in _walk(root, 3)) == 3  # caps at exactly `limit`
    assert sum(1 for _ in _walk(root, 0)) == 0  # <= 0 yields nothing
