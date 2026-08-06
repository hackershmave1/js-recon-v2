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
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

import tree_sitter_javascript as tsjs
from tree_sitter import Language, Node, Parser

from recon.findings.wrappers import WrapperRule, wrapper_callees

_LANGUAGE = Language(tsjs.language())
_PARSER = Parser(_LANGUAGE)

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_GLOBAL_OBJECTS = frozenset({"window", "globalThis", "self"})
_JQUERY = frozenset({"$", "jQuery"})
# jQuery helper -> HTTP method (config-driven ones resolve method from the config).
_JQUERY_METHODS = {"get": "GET", "post": "POST", "getJSON": "GET"}


@dataclass(frozen=True)
class RawParam:
    name: str
    location: str  # "query" | "body"


@dataclass(frozen=True)
class RawEndpoint:
    kind: str  # fetch | xhr | axios | jquery | websocket
    method: str
    url: str
    params: tuple[RawParam, ...]
    line: int
    col: int
    start_byte: int
    end_byte: int
    snippet: str
    # Provenance: the callee of the taught wrapper this endpoint came from, else
    # None. NOT folded into `kind` — `kind` stays "axios" so the POST-body
    # Content-Type gate at reconstruct.py:176 still fires (spec §7 / §12 Imp 3).
    wrapper: str | None = None


@dataclass
class Extraction:
    endpoints: list[RawEndpoint] = field(default_factory=list)
    unattributed: int = 0  # sinks detected but URL not statically resolvable (REQ-C2)


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


# --- tree helpers ------------------------------------------------------------

def _walk(node: Node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def _string_value(node: Node | None) -> str | None:
    """Resolve a string/template literal to its text; ``None`` if not static.

    Template strings keep their ``${...}`` substitutions verbatim so the shape
    survives (`/users/${id}` stays visible) instead of being dropped or guessed.
    """
    if node is None:
        return None
    if node.type == "string":
        text = _text(node)
        if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
            return text[1:-1]
        return text
    if node.type == "template_string":
        text = _text(node)
        return text[1:-1] if text.startswith("`") and text.endswith("`") else text
    return None


def _args(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    return list(arguments.named_children) if arguments is not None else []


def _object_pairs(node: Node | None) -> dict[str, Node]:
    """Map an object literal's keys to their value nodes (string + identifier keys)."""
    pairs: dict[str, Node] = {}
    if node is None or node.type != "object":
        return pairs
    for child in node.named_children:
        if child.type != "pair":
            continue
        key_node = child.child_by_field_name("key")
        value_node = child.child_by_field_name("value")
        if key_node is None or value_node is None:
            continue
        if key_node.type in ("string", "template_string"):
            key = _string_value(key_node)
        else:  # property_identifier / identifier
            key = _text(key_node)
        if key:
            pairs[key] = value_node
    return pairs


# --- base-environment collection (scope-safe pre-pass; Task 1) ---------------
#
# A pure, read-only pass that records only statically-certain, unshadowed
# base-URL bindings (spec REQ §3.1/§3.3 gate B1) so a later pass (Task 2)
# resolves `instance.get(...)` calls back to a full URL. Honesty over guessing,
# same principle as REQ-C2 above: a name that is ambiguous anywhere in the
# file — redeclared, shadowed by a parameter, or reassigned — is EXCLUDED
# rather than resolved to a possibly-wrong base. Wired into `extract()` and
# the sink handlers below by "URL resolution at the sink (Task 2)".

@dataclass(frozen=True)
class BaseEnv:
    instances: dict[str, str | None]  # axios.create var -> base literal, or None if dynamic
    default_base: str | None  # axios.defaults.baseURL literal
    const_prefixes: dict[str, str]  # const name -> string literal (for `${NAME}` prefixes)


def _declared_names(root: Node) -> set[str]:
    """Every identifier bound — or reassigned — anywhere in the tree.

    This pass has no real lexical scoping, so a name touched more than once
    (redeclared in a nested scope, shadowed by a parameter, or reassigned) is
    ambiguous and must not resolve. That's the whole point of the param-
    shadowing test: `items.forEach((loc) => ...)` re-binds `loc`, poisoning
    any outer `loc` even though the two are in different scopes. The same
    holds when the shadow arrives via destructuring/default/rest instead of
    a bare name (`({ loc }) => ...`, `function f(loc = 1)`,
    `function f(...loc)`, `const { loc } = require(...)`) — `mark()` below
    recurses into those patterns so none of them can smuggle a shadow past
    this pass (review finding, fix round 1).

    NOTE: this file parses plain JavaScript (`tree_sitter_javascript`), not
    TypeScript — a bare parameter is a plain `identifier` child of
    `formal_parameters`; the grammar never wraps it in `required_parameter`/
    `optional_parameter` (those node types are TS-only and don't exist here).

    NOTE: tree-sitter's Python bindings return a fresh wrapper object on every
    `.parent` / `.child_by_field_name` access, so `is` identity checks across
    separately-fetched nodes are unreliable even when they denote the same
    underlying node. Every check below matches on node type/field membership
    instead of identity.
    """
    seen: dict[str, int] = {}
    # Grammar-verified via `_PARSER`: object-pattern shorthand (`{ loc }`)
    # binds through a `shorthand_property_identifier_pattern` leaf, not
    # `identifier` — it doubles as both the key and the bound name.
    binding_leaf_types = ("identifier", "shorthand_property_identifier_pattern")

    def mark(candidate: Node | None) -> None:
        """Mark every binding name `candidate` introduces.

        A plain `identifier` (or an object-pattern shorthand leaf) is marked
        directly. Anything else is a destructuring/default/rest pattern:
        recurse into it and mark every binding leaf found inside. An
        object-pattern renaming key (`{ a: loc }`'s `a`) and a default
        value's expression (`x = value`'s `value`) are *references*, not
        bindings, so those subtrees are deliberately not walked — only
        `pair_pattern`'s `value` field and `assignment_pattern` /
        `object_assignment_pattern`'s `left` field are.
        """
        if candidate is None:
            return
        if candidate.type in binding_leaf_types:
            name = _text(candidate)
            seen[name] = seen.get(name, 0) + 1
        elif candidate.type == "pair_pattern":  # `{ key: value }` -- only `value` binds
            mark(candidate.child_by_field_name("value"))
        elif candidate.type in ("assignment_pattern", "object_assignment_pattern"):
            mark(candidate.child_by_field_name("left"))  # `x = default`; `default` is a read
        elif candidate.type in ("object_pattern", "array_pattern", "rest_pattern"):
            for child in candidate.named_children:
                mark(child)

    for node in _walk(root):
        if node.type in ("variable_declarator", "function_declaration"):
            mark(node.child_by_field_name("name"))
        elif node.type == "catch_clause":
            mark(node.child_by_field_name("parameter"))
        elif node.type == "arrow_function":
            mark(node.child_by_field_name("parameter"))  # bare single param: `x => ...`
        elif node.parent is not None and node.parent.type == "formal_parameters":
            mark(node)  # any param shape: plain/destructured/default/rest
        elif node.type == "assignment_expression":
            mark(node.child_by_field_name("left"))  # plain reassignment: `loc = other`
    return {name for name, count in seen.items() if count > 1}


def collect_base_env(root: Node, data: bytes) -> BaseEnv:
    # `data` isn't read here — every helper resolves text via `node.text` — but
    # stays part of the signature to match the interface Task 2 will call.
    poisoned = _declared_names(root)
    instances: dict[str, str | None] = {}
    default_base: str | None = None
    const_prefixes: dict[str, str] = {}
    for node in _walk(root):
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name_node is None or name_node.type != "identifier" or value is None:
                continue
            name = _text(name_node)
            if name in poisoned:
                continue
            if _is_axios_create(value):
                instances[name] = _base_url_arg(value)
            else:
                lit = _string_value(value)
                if lit is not None:
                    const_prefixes[name] = lit
        elif node.type == "assignment_expression":
            left = _text(node.child_by_field_name("left"))
            if left in ("axios.defaults.baseURL",):
                default_base = _string_value(node.child_by_field_name("right"))
    return BaseEnv(instances=instances, default_base=default_base, const_prefixes=const_prefixes)


def _is_axios_create(node: Node) -> bool:
    if node.type != "call_expression":
        return False
    fn = node.child_by_field_name("function")
    return fn is not None and fn.type == "member_expression" \
        and _text(fn.child_by_field_name("object")) == "axios" \
        and _text(fn.child_by_field_name("property")) == "create"


def _base_url_arg(create_call: Node) -> str | None:
    args = _args(create_call)
    if args and args[0].type == "object":
        return _string_value(_object_pairs(args[0]).get("baseURL"))
    return None


# --- param extraction --------------------------------------------------------

def _query_params(url: str) -> list[RawParam]:
    query = url.split("?", 1)[1] if "?" in url else ""
    seen: dict[str, None] = {}
    for key, _value in parse_qsl(query, keep_blank_values=True):
        if key:
            seen.setdefault(key, None)
    return [RawParam(name, "query") for name in seen]


def _body_params(node: Node | None) -> list[RawParam]:
    return [RawParam(name, "body") for name in _object_pairs(node)]


def _body_params_from_value(node: Node | None) -> list[RawParam]:
    """Body params from an object literal OR a ``JSON.stringify({...})`` wrapper
    (the near-universal way a JSON body is built)."""
    if node is None:
        return []
    if node.type == "object":
        return _body_params(node)
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and _text(fn) == "JSON.stringify":
            inner = _args(node)
            if inner and inner[0].type == "object":
                return _body_params(inner[0])
    return []


def _config_query_params(config: Node | None) -> list[RawParam]:
    """axios's ``params`` config key is serialized into the query string."""
    params_obj = _object_pairs(config).get("params")
    return [RawParam(name, "query") for name in _object_pairs(params_obj)]


def _endpoint(
    kind: str, method: str, url: str, params: list[RawParam], call: Node,
    wrapper: str | None = None,
) -> RawEndpoint:
    row, col = call.start_point
    deduped = list(dict.fromkeys(params))  # preserve order, drop repeats
    return RawEndpoint(
        kind=kind,
        method=method.upper(),
        url=url,
        params=tuple(deduped),
        line=row + 1,
        col=col,
        start_byte=call.start_byte,
        end_byte=call.end_byte,
        snippet=_text(call)[:200],
        wrapper=wrapper,
    )


# --- URL resolution at the sink (Task 2) --------------------------------------
#
# Wires `BaseEnv` (above) into the sink handlers below: an axios instance
# call, a bare axios/defaults call, and a leading `${CONST}` template prefix
# all resolve to a full path here. Honesty is preserved throughout — an
# instance with an unknown base (`env.instances[name] is None`) joins against
# `""` (path stays relative, still attributed), never guessed; a name that
# isn't a recognized instance/constant falls through to the pre-Task-2
# verbatim/unattributed behavior, unchanged.

# Any RFC 3986 scheme (a letter, then letters/digits/`+`/`.`/`-`), anchored at
# the START of the string — used by `_join_base` below. Fix round 1 (review
# Minor): the prior check was a bare `"://" in path` substring test, which
# wrongly matched a *relative* path that merely embeds a URL later on, e.g. a
# redirect query param (`/redirect?next=http://evil.com`), silently dropping
# the base. Anchoring at position 0 fixes that while still recognizing a real
# absolute URL or a protocol-relative one (`//host/x`, checked separately below).
# NOTE: named `_ABSOLUTE_SCHEME_RE` (not `_SCHEME_RE`) to avoid reader confusion
# with `normalize.py`'s own, differently-shaped `_SCHEME_RE` (captures
# scheme/slashes/rest for path normalization) — no import relationship between
# the two modules, but they sit in the same package and a shared name for two
# different patterns invites mix-ups.
_ABSOLUTE_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def _join_base(base: str, path: str) -> str:
    """Prepend `base` to `path`, unless `path` is already absolute (own scheme/host)."""
    if not base:
        return path
    if path.startswith("//") or _ABSOLUTE_SCHEME_RE.match(path):
        return path  # absolute path wins
    return base.rstrip("/") + "/" + path.lstrip("/")


def _fold_const_prefix(node: Node, env: BaseEnv) -> str | None:
    """Fold a LEADING ``${NAME}`` template substitution to its literal value.

    Grammar (verified via `_PARSER`): a `template_string`'s backtick tokens are
    unnamed, so ``named_children`` is just its fragments/substitutions in
    order; a `template_substitution`'s `${`/`}` tokens are likewise unnamed,
    leaving only the wrapped expression as its named child.

    Only the leading interpolation folds — a substitution elsewhere in the
    template (`` `prefix${API}/x` ``, or the trailing ``${id}`` in
    `` `${API}/pets/${id}` ``) is left verbatim, same as before, since only
    the first segment is a statically-certain base-style prefix (spec
    §3.1/§3.2). Returns ``None`` (caller falls back to `_string_value`'s
    normal verbatim result) unless `node` is a `template_string` that
    *starts* with a `${NAME}` substitution and `NAME` is a known constant.
    """
    if node.type != "template_string":
        return None
    named = node.named_children
    if not named or named[0].type != "template_substitution":
        return None
    substitution = named[0].named_children
    if len(substitution) != 1 or substitution[0].type != "identifier":
        return None
    prefix = env.const_prefixes.get(_text(substitution[0]))
    if prefix is None:
        return None
    text = _text(node)
    body = text[1:-1] if text.startswith("`") and text.endswith("`") else text
    leading = _text(named[0])  # e.g. "${API}"
    # Fix round 2: this is template-literal folding, not base/path joining —
    # JS evaluates `` `${API}2/pets` `` as plain string concatenation
    # (`'/v' + '2/pets'` = `/v2/pets`), never inserting a slash. Round 1
    # delegated to `_join_base` for its de-dupe behavior, but `_join_base`
    # ALWAYS inserts a separating `/` before a non-absolute remainder — right
    # for its real callers (joining a base URL to a path), wrong here, where
    # it fabricated a slash the source never had (`/v` + `2/pets` wrongly
    # became `/v/2/pets`) and appended one to a bare substitution with no
    # trailing text (`/v3` + `` wrongly became `/v3/`). The only case that
    # still needs de-duping is the genuinely ambiguous boundary where the
    # remainder itself starts with `/` (a prefix stored with its own trailing
    # slash, e.g. `const API = '/v3/'`, joined against `${API}/pets`) — handle
    # that one case directly instead of routing through `_join_base`.
    remainder = body[len(leading):]
    if remainder.startswith("/"):
        return prefix.rstrip("/") + remainder  # de-dupe only the ambiguous boundary
    return prefix + remainder  # pure template concatenation, no inserted slash


def _resolve_url(node: Node | None, env: BaseEnv, base: str) -> str | None:
    """Resolve a sink's URL-argument node to a base-joined, prefix-folded string.

    ``None`` means "not statically resolvable" (REQ-C2 honesty) — identical to
    what `_string_value` alone would say; folding/joining only ever turns a
    resolvable relative path into a fuller one, never turns an unresolvable
    node into a guessed one.
    """
    if node is None:
        return None
    folded = _fold_const_prefix(node, env)
    url = folded if folded is not None else _string_value(node)
    if url is None:
        return None
    return _join_base(base, url)


# --- sink handlers -----------------------------------------------------------

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
        prop = _string_value(fn.child_by_field_name("index"))
        if prop is None:  # dynamic index -> can't attribute a method name
            return
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


def _fetch(call: Node, result: Extraction, env: BaseEnv, base: str = "") -> None:
    args = _args(call)
    url = _resolve_url(args[0], env, base) if args else None
    if url is None:
        result.unattributed += 1
        return
    method, params = "GET", _query_params(url)
    if len(args) >= 2 and args[1].type == "object":
        options = _object_pairs(args[1])
        method = (_string_value(options.get("method")) or "GET").upper()
        params += _body_params_from_value(options.get("body"))
    result.endpoints.append(_endpoint("fetch", method, url, params, call))


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
        result.unattributed += 1
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
            result.unattributed += 1
            return
        method = "GET"
        if len(args) >= 2 and args[1].type == "object":
            method = (_string_value(_object_pairs(args[1]).get("method")) or "GET").upper()
        result.endpoints.append(_endpoint("axios", method, url, _query_params(url), call))


def _axios_member(
    call: Node, prop: str, result: Extraction, env: BaseEnv, base: str = "",
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
        result.unattributed += 1
        return
    params = _query_params(url)
    if prop.upper() in ("POST", "PUT", "PATCH"):
        # axios.post(url, data[, config])
        if len(args) >= 2:
            params += _body_params_from_value(args[1])
        if len(args) >= 3:
            params += _config_query_params(args[2])
    elif len(args) >= 2:
        # axios.get/delete/head(url[, config]) — query params live in the config
        params += _config_query_params(args[1])
    result.endpoints.append(_endpoint("axios", prop, url, params, call, wrapper=wrapper))


def _axios_from_config(
    config: Node, call: Node, result: Extraction, env: BaseEnv, base: str = "",
    wrapper: str | None = None,
) -> None:
    pairs = _object_pairs(config)
    url = _resolve_url(pairs.get("url"), env, base)
    if url is None:
        result.unattributed += 1
        return
    method = (_string_value(pairs.get("method")) or "GET").upper()
    params = (
        _query_params(url)
        + _config_query_params(config)  # axios `params` -> query, not body
        + _body_params_from_value(pairs.get("data"))
    )
    result.endpoints.append(_endpoint("axios", method, url, params, call, wrapper=wrapper))


def _jquery(call: Node, prop: str, result: Extraction) -> None:
    args = _args(call)
    if prop == "ajax":
        config = args[0] if args and args[0].type == "object" else None
        pairs = _object_pairs(config)
        url = _string_value(pairs.get("url"))
        if url is None:
            result.unattributed += 1
            return
        method = (_string_value(pairs.get("type")) or _string_value(pairs.get("method")) or "GET").upper()
        # jQuery sends `data` as the query string for GET/HEAD, else as the body.
        location = "query" if method in ("GET", "HEAD") else "body"
        params = _query_params(url) + [
            RawParam(name, location) for name in _object_pairs(pairs.get("data"))
        ]
        result.endpoints.append(_endpoint("jquery", method, url, params, call))
    elif prop in _JQUERY_METHODS:
        url = _string_value(args[0]) if args else None
        if url is None:
            result.unattributed += 1
            return
        params = _query_params(url)
        if len(args) >= 2 and args[1].type == "object":
            location = "body" if prop == "post" else "query"  # $.get sends query
            params += [RawParam(name, location) for name in _object_pairs(args[1])]
        result.endpoints.append(
            _endpoint("jquery", _JQUERY_METHODS[prop], url, params, call)
        )


def _handle_new(new: Node, result: Extraction) -> None:
    constructor = new.child_by_field_name("constructor")
    name = _text(constructor).split(".")[-1]  # WebSocket or window.WebSocket
    if name != "WebSocket":
        return
    args = _args(new)
    url = _string_value(args[0]) if args else None
    if url is None:
        result.unattributed += 1
        return
    method = "WSS" if url.lower().startswith("wss") else "WS"
    result.endpoints.append(_endpoint("websocket", method, url, _query_params(url), new))
