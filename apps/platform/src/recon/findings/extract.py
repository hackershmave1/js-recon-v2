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

from collections.abc import Sequence

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
    _looks_like_api_path,
    _object_pairs,
    _query_params,
    _string_value,
    _text,
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


def extract(source: str | bytes, wrappers: Sequence[WrapperRule] = ()) -> Extraction:
    """Extract network endpoints from JavaScript source.

    `wrappers` names custom HTTP-client callees (`api`, `apiClient`) whose member
    calls are recognized via the axios path (spec §4); empty = today's fixed set only.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = _PARSER.parse(data)
    env = collect_base_env(tree.root_node, data)
    callees = wrapper_callees(wrappers)
    result = Extraction()
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            _handle_call(node, result, env, callees)
        elif node.type == "new_expression":
            _handle_new(node, result)
    return result


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
    if fn.type == "member_expression":
        obj = _text(fn.child_by_field_name("object"))
        prop = _text(fn.child_by_field_name("property"))
    elif fn.type == "subscript_expression":
        obj = _text(fn.child_by_field_name("object"))
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
    if prop == "fetch" and obj in _GLOBAL_OBJECTS:
        _fetch(call, result, env)
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
    name = _text(constructor).split(".")[-1]  # WebSocket or window.WebSocket
    if name != "WebSocket":
        return
    args = _args(new)
    url = _string_value(args[0]) if args else None
    if url is None:
        _record_unresolved(result, "websocket", "WS", args[0] if args else None, new)
        return
    method = "WSS" if url.lower().startswith("wss") else "WS"
    result.endpoints.append(_endpoint("websocket", method, url, _query_params(url), new))
