"""In-process JS static analysis ("Vespasian") — trace network calls in a bundle.

Walks a JavaScript AST (tree-sitter) for the network sinks a client uses to talk
to its backend — ``fetch``, ``XMLHttpRequest.open``, ``axios.*``, jQuery
``$.ajax/$.get/$.post``, and ``new WebSocket`` — and reconstructs each call's HTTP
method, URL, and statically-determinable params.

Honesty over guessing (REQ-C2): a sink we detect but whose URL is not statically
resolvable (a bare variable, a runtime concatenation) is NOT invented — it is
counted in ``Extraction.unattributed`` so coverage can be reported truthfully.
An axios instance (``axios.create(...)``) is always attributed once recognized,
even when its base isn't statically known — the path is kept relative rather
than guessed, but the call still lands in ``endpoints``, never a silent drop.
Downstream, each :class:`RawEndpoint` is normalized (recon.findings.normalize)
and written through the outbox (recon.findings.store).

Known MVP limitations (no data-flow analysis): a library aliased to another name
(``const a = axios; a.get(...)``) is not resolved and leaves no trace; a URL built
by concatenation or held in a variable is counted as unattributed, never guessed;
and ``.open(<method-string>, <url>)`` on a non-XHR receiver can be a rare false
positive since the receiver's type isn't tracked.

The AST primitives (parser, dataclasses, tree/param helpers) live in
:mod:`recon.findings._jsast` and the base-URL resolution unit in
:mod:`recon.findings._base_env` (DEBT D11 split); this module keeps the public
``extract`` entrypoint and the sink handlers. ``RawEndpoint`` and ``HTTP_METHODS``
are re-exported here (see ``__all__``) because downstream modules
(``recon.findings.analyze``, ``recon.spec.classify``) import them from this path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from tree_sitter import Node

from recon.findings._base_env import _resolve_url, collect_base_env
from recon.findings._jsast import (
    _GENERIC_METHODS,
    _GLOBAL_OBJECTS,
    _JQUERY,
    _JQUERY_METHODS,
    _PARSER,
    HTTP_METHODS,
    BaseEnv,
    Extraction,
    HeaderRef,
    RawEndpoint,
    RawParam,
    _args,
    _auth_headers,
    _body_params_from_value,
    _collapse_url,
    _config_query_params,
    _endpoint,
    _is_http_client_name,
    _looks_api_ish,
    _looks_like_api_path,
    _looks_like_route,
    _object_pairs,
    _query_params,
    _source_snippet,
    _string_value,
    _text,
    _text_if_short,
    _walk,
)
from recon.findings.wrappers import WrapperRule, wrapper_callees

__all__ = [
    "HTTP_METHODS",
    "Extraction",
    "RawEndpoint",
    "RawParam",
    "collect_base_env",
    "extract",
]


def extract(
    source: str | bytes,
    wrappers: Sequence[WrapperRule] = (),
    *,
    cross_module_consts: Mapping[str, str] | None = None,
) -> Extraction:
    """Extract network endpoints from JavaScript source.

    `wrappers` names custom HTTP-client callees (`api`, `apiClient`) whose member
    calls are recognized via the axios path (spec §4); empty = today's fixed set only.

    `cross_module_consts` maps a name this unit IMPORTS from another module to that
    exporter's string literal (built by recon.findings._modulegraph from a run-level
    export index). It lets the sink resolver fold a cross-chunk `fetch(API_BASE +
    ORDERS_PATH)` whose operands live in a sibling chunk; empty/None = today's
    per-file behavior, unchanged.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = _PARSER.parse(data)
    env = collect_base_env(tree.root_node, data)
    if cross_module_consts:
        env = replace(env, cross_module_consts=dict(cross_module_consts))
    callees = wrapper_callees(wrappers)
    result = Extraction()
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            _handle_call(node, result, env, callees)
        elif node.type == "new_expression":
            _handle_new(node, result)
        elif node.type == "pair":  # object-literal href/src/action value -> a page route
            _handle_property_url(node, result)
    _harvest_routes(tree.root_node, result)  # off-sink absolute-URL literals, after the sinks
    _fill_snippets(result, data)  # after all lanes (incl. routes) are populated
    return result


def _fill_snippets(result: Extraction, data: bytes) -> None:
    """Fill each endpoint's deferred display snippet from the source bytes, now the walk is
    done and `data` is in hand. `_endpoint` leaves ``snippet=""`` because building it there
    from ``call.text`` is O(node span) — O(n^2) over a nested-sink chain (DoS); `_source_snippet`
    slices the source O(cap) with byte-identical output. Rebuilds each frozen row in place."""
    for lane in (result.endpoints, result.unresolved, result.generic, result.routes):
        lane[:] = [
            replace(ep, snippet=_source_snippet(data, ep.start_byte, ep.end_byte)) for ep in lane
        ]


# --- sink handlers -----------------------------------------------------------


def _record_unresolved(
    result: Extraction,
    kind: str,
    method: str,
    url_node: Node | None,
    call: Node,
    wrapper: str | None = None,
) -> None:
    """Surface a detected sink whose URL isn't statically resolvable (Tier 4, the
    unconfirmed lane). Still increments the REQ-C2 ``unattributed`` counter (honesty
    unchanged — the call IS unattributed) AND emits a best-effort ``_collapse_url``
    skeleton row so the call is visible to the analyst instead of silently dropped."""
    result.unattributed += 1
    result.unresolved.append(
        _endpoint(kind, method, _collapse_url(url_node), [], call, wrapper=wrapper)
    )


def _record_generic(call: Node, prop: str, result: Extraction) -> None:
    """Tier 5 (generic-call): a verb call on an unrecognised but HTTP-client-shaped receiver
    whose first arg is path-shaped — a SUSPECTED custom/untaught client.

    Unlike Tier-4 ``_record_unresolved`` this does NOT touch the REQ-C2 ``unattributed``
    counter: it is a *suspected*, not a *detected*, sink, so moving the honesty counter would
    over-report coverage. It rides the separate ``generic`` list, surfaced later as a distinct
    ENDPOINT_GENERIC finding. The receiver/method gates are applied by the caller
    (``_dispatch_member``); here only the argument's path shape is enforced, so a verb call
    with a non-path arg (a Map key, a lodash dotted path) records nothing."""
    args = _args(call)
    skeleton = _collapse_url(args[0] if args else None)
    if not _looks_like_api_path(skeleton):
        return
    result.generic.append(_endpoint("generic", prop.upper(), skeleton, [], call))


# --- page routes (Phase 2): href/src/action, nav sinks, off-sink URL literals ------------
# A distinct category from the API lanes: a client-side navigation target rather than a
# backend call. Detected from object `href`/`src`/`action` values, client-navigation sinks
# (`location.assign`, `history.pushState`, `router.push`), and off-sink absolute-URL
# literals; FP-gated by `_looks_like_route`; drained as FindingType.PAGE_ROUTE.

_ROUTE_KEYS = frozenset({"href", "src", "action"})
# Pseudo-schemes / fragments that are never a navigable page route. Some embed `://`
# (`javascript://…`, `blob:https://…`), so they must be rejected BEFORE the path anchor.
_ROUTE_SCHEME_REJECTS = ("#", "mailto:", "tel:", "javascript:", "data:", "blob:", "about:")
# Absolute URLs that pervade bundles as namespace / spec identifiers, never a page the app
# navigates to (SVG/XML/XHTML `xmlns`, schema.org microdata, the RFC example domain).
# Harvest-only: they carry a `://` so they would otherwise pass the route gate.
_HARVEST_HOST_DENY = ("w3.org", "schema.org", "ns.adobe.com", "purl.org", "example.com")
# A URL builder wider than this many source bytes is not a route — skip it without decoding
# (defence-in-depth against a pathological single top-level concat/`+` chain).
_MAX_HARVEST_SPAN = 8192
# Explicit global roots that make a nav-sink receiver unambiguous (`window.location.assign`,
# `document.location.replace`, `window.open`). A BARE `location`/`history`/`router` could be a
# shadowing local or an array, so a text-only match is recorded LOW, not HIGH (§4 review).
_ROUTE_GLOBAL_ROOTS = _GLOBAL_OBJECTS | {"document"}


def _record_route(
    url_node: Node | None, result: Extraction, *, confidence: str, call: Node | None = None
) -> None:
    """Reconstruct a page-route URL from ``url_node``, gate it (pseudo-scheme pre-rejects +
    the ``_looks_like_route`` FP gate), and record it with a BLANK method so the value reads
    ``/player/:id``, not ``GET /player/:id``. ``call`` overrides which node's byte span is
    stored as evidence — a nav sink stores the whole call, an href its value — and that span
    is what the harvest pass treats as claimed, so a URL recorded here is never re-emitted."""
    if url_node is None:
        return
    skeleton = _collapse_url(url_node)
    if skeleton.lower().startswith(_ROUTE_SCHEME_REJECTS) or not _looks_like_route(skeleton):
        return
    result.routes.append(
        _endpoint("route", "", skeleton, [], call or url_node, confidence=confidence)
    )


def _handle_property_url(pair: Node, result: Extraction) -> None:
    """An object-literal ``href``/``src``/``action`` value -> a page route. LOW confidence:
    these keys also appear in request bodies/config, so precision rests on the FP gate + the
    value's own shape rather than on proving DOM context (design §4 Finding 5e)."""
    key_node = pair.child_by_field_name("key")
    value_node = pair.child_by_field_name("value")
    if key_node is None or value_node is None:
        return
    key = (
        _string_value(key_node)
        if key_node.type in ("string", "template_string")
        else _text(key_node)
    )
    if key in _ROUTE_KEYS:
        _record_route(value_node, result, confidence="low")


def _nav_route_arg(obj: str, prop: str) -> int | None:
    """The positional index of the URL argument for a client-navigation sink, or ``None`` if
    ``obj.prop`` is not one. These receivers (``location``/``history``/``router``) sit in the
    Tier-5 non-HTTP denylist; here they are RECLAIMED for the route lane. The confidence is
    decided at record time (``_record_nav_route``), high only for an explicit global receiver.
    ``history.pushState(state, title, url)`` carries the URL third; the rest carry it first."""
    tail = obj.rsplit(".", 1)[-1]
    if tail == "location" and prop in ("assign", "replace"):
        return 0
    if obj in _GLOBAL_OBJECTS and prop == "open":  # window.open(url, ...)
        return 0
    if tail == "history" and prop in ("pushState", "replaceState"):
        return 2
    if tail == "router" and prop in ("push", "replace", "navigate"):
        return 0
    return None


def _record_nav_route(call: Node, obj: str, arg_index: int, result: Extraction) -> None:
    """A client-navigation sink -> a page route. HIGH confidence only when the receiver is an
    explicit global (``window.``/``document.``/``self.``-anchored, or ``window.open`` itself);
    a bare ``location``/``history``/``router`` receiver is text-only — it could be a shadowing
    local or an array — so it is recorded LOW (§4 review). ``router.push({pathname})`` /
    ``({path})`` / ``({url})`` is unwrapped from its object form; otherwise the URL is the
    positional argument at ``arg_index``."""
    args = _args(call)
    if arg_index >= len(args):
        return
    node = args[arg_index]
    target: Node | None = node
    if node.type == "object":  # router.push({ pathname: "/x" })
        pairs = _object_pairs(node)
        target = pairs.get("pathname") or pairs.get("path") or pairs.get("url")
    confidence = "high" if obj.split(".", 1)[0] in _ROUTE_GLOBAL_ROOTS else "low"
    _record_route(target, result, confidence=confidence, call=call)


def _is_concat_call(node: Node) -> bool:
    """``a.concat(...)`` — the only ``call_expression`` shape that reconstructs to a URL. Kept
    cheap (inspects the callee's property, never the whole node text) so the harvest walk
    doesn't decode giant wrapper calls just to skip them."""
    fn = node.child_by_field_name("function")
    return (
        fn is not None
        and fn.type == "member_expression"
        and _text(fn.child_by_field_name("property")) == "concat"
    )


def _is_absolute_url(skeleton: str) -> bool:
    """A scheme-absolute URL (``https://…``, ``ws://…``), NOT a rooted path that merely embeds
    a URL in a query (``/redirect?to=http://…``). Off-sink harvesting is absolute-only: a bare
    ``/path`` with no sink/href context is too ambiguous to claim as a route (user decision)."""
    scheme, sep, _rest = skeleton.partition("://")
    return bool(sep) and scheme != "" and "/" not in scheme and " " not in scheme


def _harvest_routes(root: Node, result: Extraction) -> None:
    """Second pass: harvest OFF-SINK absolute-URL literals — a ``.concat()``/``+``-built
    ``https://…`` that is returned or assigned, never passed to a sink (user pt 1). A
    top-level-expression guard — the claimed byte spans of every already-recorded sink/route
    AND each harvest — stops a nested concat, and a sink's own URL arg, from double-emitting.
    Context-free, so classified by shape (``_looks_api_ish``): API-ish -> the generic
    suspected-API lane; else -> a LOW-confidence page route."""
    claimed = [
        (ep.start_byte, ep.end_byte)
        for ep in (*result.endpoints, *result.unresolved, *result.generic, *result.routes)
    ]
    for node in _walk(root):
        is_builder = node.type in ("string", "template_string", "binary_expression") or (
            node.type == "call_expression" and _is_concat_call(node)
        )
        if not is_builder:
            continue
        # O(1), decode-free guards FIRST — a span cap, then the claimed-range check — so `_text`
        # (which decodes the node's whole subtree) is only ever reached for a small, unclaimed
        # builder. `node.parent` is deliberately unused: it is O(depth) in tree-sitter (re-roots
        # from the top), so a per-node parent walk is itself O(n^2) on a deep chain (§4 review).
        # Preorder + the claimed span dedup nested builders: the outer is harvested first and
        # claims its span, so its inners are skipped here; an oversized outer is span-capped out
        # (a multi-KB "URL" is not a route) and its inners then carry no `://` — nothing emitted.
        if node.end_byte - node.start_byte > _MAX_HARVEST_SPAN:
            continue
        if any(start <= node.start_byte and node.end_byte <= end for start, end in claimed):
            continue  # inside a recorded sink/route, or a nested sub-expression already taken
        if "://" not in _text(node):  # "://": absolute-only + cheap pre-skip
            continue
        skeleton = _collapse_url(node)
        lowered = skeleton.lower()
        if not _is_absolute_url(skeleton) or any(deny in lowered for deny in _HARVEST_HOST_DENY):
            continue
        if not _looks_like_route(skeleton):
            continue
        if _looks_api_ish(skeleton):
            result.generic.append(_endpoint("generic", "", skeleton, [], node))
        else:
            result.routes.append(_endpoint("route", "", skeleton, [], node, confidence="low"))
        claimed.append((node.start_byte, node.end_byte))


def _handle_call(call: Node, result: Extraction, env: BaseEnv, callees: frozenset[str]) -> None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return
    if fn.type == "identifier":
        name = _text(fn)
        if name == "fetch":
            _fetch(call, result, env)
        elif name == "axios":
            _axios_call(call, result, env, base=env.default_base or "")
        return
    # Member access, dotted (axios.get) or computed (axios["get"]) — the latter is
    # common in property-mangled bundles and must not be silently dropped (C2).
    # `_text_if_short` bounds the receiver decode: a huge receiver (a nested `.concat()`/`+`
    # chain used as a call object) matches no dispatch branch anyway, so skipping its decode
    # keeps the walk linear on the string-splitting DoS shape (see `_text_if_short`).
    if fn.type == "member_expression":
        obj = _text_if_short(fn.child_by_field_name("object"))
        prop = _text(fn.child_by_field_name("property"))
    elif fn.type == "subscript_expression":
        obj = _text_if_short(fn.child_by_field_name("object"))
        index = _string_value(fn.child_by_field_name("index"))
        if index is None:  # dynamic index -> can't attribute a method name
            return
        prop = index
    else:
        return
    _dispatch_member(call, obj, prop, result, env, callees)


def _dispatch_member(
    call: Node, obj: str, prop: str, result: Extraction, env: BaseEnv, callees: frozenset[str]
) -> None:
    nav_arg = _nav_route_arg(obj, prop)
    if prop == "fetch" and obj in _GLOBAL_OBJECTS:
        _fetch(call, result, env)
    elif (
        nav_arg is not None
    ):  # client-navigation sink -> page route; before .open so window.open wins
        _record_nav_route(call, obj, nav_arg, result)
    elif prop == "open":  # ANY receiver's `.open(method, url)` is XHR, checked before instances
        _xhr_open(call, result)
    elif obj == "axios":
        _axios_member(call, prop, result, env, base=env.default_base or "")
    elif obj in _JQUERY:
        _jquery(call, prop, result)
    elif obj in env.instances:
        base = env.instances[obj]  # may be None (recognized instance, unknown base)
        _axios_member(call, prop, result, env, base=base or "")
    elif obj in callees:  # taught wrapper — MUST be last so native/instance collisions win
        _axios_member(call, prop, result, env, base="", wrapper=obj)
    elif prop in _GENERIC_METHODS and _is_http_client_name(obj):
        # Tier 5 (generic-call): unrecognised receiver + verb method + path-shaped arg = a
        # SUSPECTED custom client. STRICTLY last — every real sink, axios instance, and taught
        # wrapper above wins first; this only sees receivers nothing else claimed.
        _record_generic(call, prop, result)


def _fetch(call: Node, result: Extraction, env: BaseEnv, base: str = "") -> None:
    args = _args(call)
    url = _resolve_url(args[0], env, base) if args else None
    if url is None:
        method = "GET"
        if len(args) >= 2 and args[1].type == "object":
            method = (_string_value(_object_pairs(args[1]).get("method")) or "GET").upper()
        _record_unresolved(result, "fetch", method, args[0] if args else None, call)
        return
    method, params = "GET", _query_params(url)
    headers: list[HeaderRef] = []
    if len(args) >= 2 and args[1].type == "object":
        options = _object_pairs(args[1])
        method = (_string_value(options.get("method")) or "GET").upper()
        params += _body_params_from_value(options.get("body"))
        headers = _auth_headers(options.get("headers"))
    result.endpoints.append(_endpoint("fetch", method, url, params, call, headers=headers))


def _xhr_open(call: Node, result: Extraction) -> None:
    args = _args(call)
    method = _string_value(args[0]) if args else None
    # NOTE: XHR is inferred from the `.open(<method>, <url>)` shape, not from
    # tracking that the receiver is an XMLHttpRequest (no data-flow). This can
    # rarely false-positive on another `.open(<http-method-string>, <str>)` API.
    if method is None or method.upper() not in HTTP_METHODS:
        return  # a `.open(...)` on something that isn't an XHR
    url = _string_value(args[1]) if len(args) >= 2 else None
    if url is None:
        _record_unresolved(result, "xhr", method.upper(), args[1] if len(args) >= 2 else None, call)
        return
    result.endpoints.append(_endpoint("xhr", method, url, _query_params(url), call))


def _axios_call(call: Node, result: Extraction, env: BaseEnv, base: str = "") -> None:
    # axios(config) or axios(url, config)
    args = _args(call)
    if args and args[0].type == "object":
        _axios_from_config(args[0], call, result, env, base=base)
    elif args:
        url = _resolve_url(args[0], env, base)
        if url is None:
            method = "GET"
            if len(args) >= 2 and args[1].type == "object":
                method = (_string_value(_object_pairs(args[1]).get("method")) or "GET").upper()
            _record_unresolved(result, "axios", method, args[0], call)
            return
        method = "GET"
        headers: list[HeaderRef] = []
        if len(args) >= 2 and args[1].type == "object":
            cfg = _object_pairs(args[1])
            method = (_string_value(cfg.get("method")) or "GET").upper()
            headers = _auth_headers(cfg.get("headers"))
        result.endpoints.append(
            _endpoint("axios", method, url, _query_params(url), call, headers=headers)
        )


def _axios_member(
    call: Node,
    prop: str,
    result: Extraction,
    env: BaseEnv,
    base: str = "",
    wrapper: str | None = None,
) -> None:
    args = _args(call)
    if prop == "request" and args and args[0].type == "object":
        _axios_from_config(args[0], call, result, env, base=base, wrapper=wrapper)
        return
    if prop.upper() not in HTTP_METHODS:
        return
    url = _resolve_url(args[0], env, base) if args else None
    if url is None:
        _record_unresolved(
            result, "axios", prop.upper(), args[0] if args else None, call, wrapper=wrapper
        )
        return
    params = _query_params(url)
    config: Node | None = None
    if prop.upper() in ("POST", "PUT", "PATCH"):
        # axios.post(url, data[, config])
        if len(args) >= 2:
            params += _body_params_from_value(args[1])
        if len(args) >= 3:
            config = args[2]
            params += _config_query_params(config)
    elif len(args) >= 2:
        # axios.get/delete/head(url[, config]) — query params + headers live in the config
        config = args[1]
        params += _config_query_params(config)
    headers = _auth_headers(_object_pairs(config).get("headers"))
    result.endpoints.append(
        _endpoint("axios", prop, url, params, call, wrapper=wrapper, headers=headers)
    )


def _axios_from_config(
    config: Node,
    call: Node,
    result: Extraction,
    env: BaseEnv,
    base: str = "",
    wrapper: str | None = None,
) -> None:
    pairs = _object_pairs(config)
    url = _resolve_url(pairs.get("url"), env, base)
    if url is None:
        method = (_string_value(pairs.get("method")) or "GET").upper()
        _record_unresolved(result, "axios", method, pairs.get("url"), call, wrapper=wrapper)
        return
    method = (_string_value(pairs.get("method")) or "GET").upper()
    params = (
        _query_params(url)
        + _config_query_params(config)  # axios `params` -> query, not body
        + _body_params_from_value(pairs.get("data"))
    )
    headers = _auth_headers(pairs.get("headers"))
    result.endpoints.append(
        _endpoint("axios", method, url, params, call, wrapper=wrapper, headers=headers)
    )


def _jquery(call: Node, prop: str, result: Extraction) -> None:
    args = _args(call)
    if prop == "ajax":
        config = args[0] if args and args[0].type == "object" else None
        pairs = _object_pairs(config)
        url = _string_value(pairs.get("url"))
        if url is None:
            method = (
                _string_value(pairs.get("type")) or _string_value(pairs.get("method")) or "GET"
            ).upper()
            _record_unresolved(result, "jquery", method, pairs.get("url"), call)
            return
        method = (
            _string_value(pairs.get("type")) or _string_value(pairs.get("method")) or "GET"
        ).upper()
        # jQuery sends `data` as the query string for GET/HEAD, else as the body.
        location = "query" if method in ("GET", "HEAD") else "body"
        params = _query_params(url) + [
            RawParam(name, location) for name in _object_pairs(pairs.get("data"))
        ]
        result.endpoints.append(_endpoint("jquery", method, url, params, call))
    elif prop in _JQUERY_METHODS:
        url = _string_value(args[0]) if args else None
        if url is None:
            _record_unresolved(
                result, "jquery", _JQUERY_METHODS[prop], args[0] if args else None, call
            )
            return
        params = _query_params(url)
        if len(args) >= 2 and args[1].type == "object":
            location = "body" if prop == "post" else "query"  # $.get sends query
            params += [RawParam(name, location) for name in _object_pairs(args[1])]
        result.endpoints.append(_endpoint("jquery", _JQUERY_METHODS[prop], url, params, call))


def _handle_new(new: Node, result: Extraction) -> None:
    constructor = new.child_by_field_name("constructor")
    # `_text_if_short` bounds the decode: a >cap constructor can't be `WebSocket` (a nested
    # `new WebSocket(new WebSocket(…))` DoS shape has a huge inner constructor), so treat it
    # as non-matching instead of decoding the whole subtree per `new` node.
    name = _text_if_short(constructor).split(".")[-1]  # WebSocket or window.WebSocket
    if name != "WebSocket":
        return
    args = _args(new)
    url = _string_value(args[0]) if args else None
    if url is None:
        _record_unresolved(result, "websocket", "WS", args[0] if args else None, new)
        return
    method = "WSS" if url.lower().startswith("wss") else "WS"
    result.endpoints.append(_endpoint("websocket", method, url, _query_params(url), new))
