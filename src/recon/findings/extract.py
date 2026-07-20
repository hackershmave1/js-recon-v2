"""In-process JS static analysis ("Vespasian") — trace network calls in a bundle.

Walks a JavaScript AST (tree-sitter) for the network sinks a client uses to talk
to its backend — ``fetch``, ``XMLHttpRequest.open``, ``axios.*``, jQuery
``$.ajax/$.get/$.post``, and ``new WebSocket`` — and reconstructs each call's HTTP
method, URL, and statically-determinable params.

Honesty over guessing (REQ-C2): a sink we detect but whose URL is not statically
resolvable (a bare variable, a runtime concatenation) is NOT invented — it is
counted in ``Extraction.unattributed`` so coverage can be reported truthfully.
Downstream, each :class:`RawEndpoint` is normalized (recon.findings.normalize)
and written through the outbox (recon.findings.store).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from tree_sitter import Language, Node, Parser
import tree_sitter_javascript as tsjs

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


@dataclass
class Extraction:
    endpoints: list[RawEndpoint] = field(default_factory=list)
    unattributed: int = 0  # sinks detected but URL not statically resolvable (REQ-C2)


def extract(source: str | bytes) -> Extraction:
    """Extract network endpoints from JavaScript source."""
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = _PARSER.parse(data)
    result = Extraction()
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            _handle_call(node, result)
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


def _endpoint(kind: str, method: str, url: str, params: list[RawParam], call: Node) -> RawEndpoint:
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
    )


# --- sink handlers -----------------------------------------------------------

def _handle_call(call: Node, result: Extraction) -> None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return
    if fn.type == "identifier":
        name = _text(fn)
        if name == "fetch":
            _fetch(call, result)
        elif name == "axios":
            _axios_call(call, result)
    elif fn.type == "member_expression":
        obj = _text(fn.child_by_field_name("object"))
        prop = _text(fn.child_by_field_name("property"))
        if prop == "fetch" and obj in _GLOBAL_OBJECTS:
            _fetch(call, result)
        elif prop == "open":
            _xhr_open(call, result)
        elif obj == "axios":
            _axios_member(call, prop, result)
        elif obj in _JQUERY:
            _jquery(call, prop, result)


def _fetch(call: Node, result: Extraction) -> None:
    args = _args(call)
    url = _string_value(args[0]) if args else None
    if url is None:
        result.unattributed += 1
        return
    method, params = "GET", _query_params(url)
    if len(args) >= 2 and args[1].type == "object":
        options = _object_pairs(args[1])
        method = (_string_value(options.get("method")) or "GET").upper()
        params += _body_params(options.get("body"))
    result.endpoints.append(_endpoint("fetch", method, url, params, call))


def _xhr_open(call: Node, result: Extraction) -> None:
    args = _args(call)
    method = _string_value(args[0]) if args else None
    if method is None or method.upper() not in HTTP_METHODS:
        return  # a `.open(...)` on something that isn't an XHR
    url = _string_value(args[1]) if len(args) >= 2 else None
    if url is None:
        result.unattributed += 1
        return
    result.endpoints.append(_endpoint("xhr", method, url, _query_params(url), call))


def _axios_call(call: Node, result: Extraction) -> None:
    # axios(config) or axios(url, config)
    args = _args(call)
    if args and args[0].type == "object":
        _axios_from_config(args[0], call, result)
    elif args:
        url = _string_value(args[0])
        if url is None:
            result.unattributed += 1
            return
        method = "GET"
        if len(args) >= 2 and args[1].type == "object":
            method = (_string_value(_object_pairs(args[1]).get("method")) or "GET").upper()
        result.endpoints.append(_endpoint("axios", method, url, _query_params(url), call))


def _axios_member(call: Node, prop: str, result: Extraction) -> None:
    args = _args(call)
    if prop == "request" and args and args[0].type == "object":
        _axios_from_config(args[0], call, result)
        return
    if prop.upper() not in HTTP_METHODS:
        return
    url = _string_value(args[0]) if args else None
    if url is None:
        result.unattributed += 1
        return
    result.endpoints.append(_endpoint("axios", prop, url, _query_params(url), call))


def _axios_from_config(config: Node, call: Node, result: Extraction) -> None:
    pairs = _object_pairs(config)
    url = _string_value(pairs.get("url"))
    if url is None:
        result.unattributed += 1
        return
    method = (_string_value(pairs.get("method")) or "GET").upper()
    params = _query_params(url) + _body_params(pairs.get("data")) + _body_params(pairs.get("params"))
    result.endpoints.append(_endpoint("axios", method, url, params, call))


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
        params = _query_params(url) + _body_params(pairs.get("data"))
        result.endpoints.append(_endpoint("jquery", method, url, params, call))
    elif prop in _JQUERY_METHODS:
        url = _string_value(args[0]) if args else None
        if url is None:
            result.unattributed += 1
            return
        result.endpoints.append(
            _endpoint("jquery", _JQUERY_METHODS[prop], url, _query_params(url), call)
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
