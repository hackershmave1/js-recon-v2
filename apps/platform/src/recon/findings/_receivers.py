"""What a `.get()`/`.post()` receiver IS, decided by what the bundle itself says about it.

A call like ``this.idNodeMap.get(e)`` is shaped exactly like ``this.http.get(url)``; a receiver's
NAME cannot tell them apart (that guess counted 61 Map/Set lookups in one real capture as
unresolved HTTP calls). The code can: the same bundle almost always shows how a receiver was
created and what else is done with it. Evidence is collected per receiver text (``this.X`` or a
local name; never a bare ``this``) during :func:`recon.findings._base_env.collect_base_env`'s
existing walk — no extra tree pass (DEBT D21):

- HTTP: created by ``axios.create(...)``, or used via ``.interceptors`` (an axios/Angular client's
  own API). Deliberately NOT ``.request``/``.defaults``: a FetchEvent/AxiosError ``e.request`` and
  Chart.js/lodash ``.defaults`` are everywhere in bundles and say nothing about the receiver.
- COLLECTION: created as ``new Map/Set/WeakMap/...`` (declaration, assignment or class field), or
  used with a member no HTTP client has (``.has/.set/.add/.clear/.forEach/.keys/...``).
- INJECTED: handed in from outside — assigned from a constructor/function parameter
  (``constructor(e){this.http=e}``, an ES5 ``function S(e){this.http=e}``), from ``inject(...)``
  (Angular 14+), or from a factory ``X.create(...)`` (ky/axios minified as ``n.a.create``).

Verdict (:func:`kind_of`): HTTP evidence -> HTTP. Collection evidence alone -> COLLECTION (the call
is dropped). Injected alone -> HTTP (the DI-delivered client). Injected AND collection evidence on
one key (two classes reusing a field name, or an injected cache) -> no verdict: the call falls to
the SUSPECTED lane rather than being dropped or confirmed on contradictory evidence.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from recon.findings._jsast import _text, _text_if_short

COLLECTION = "collection"
HTTP = "http"

# Evidence flags (bitmask per receiver).
_INJECTED, _COLLECTION_SEEN, _HTTP_SEEN = 1, 2, 4

_COLLECTION_CTORS = frozenset(
    {"Map", "Set", "WeakMap", "WeakSet", "Array", "URLSearchParams", "Headers", "FormData"}
)
# Members only a collection has (not `get`/`delete`: those are the HTTP verbs being judged).
_COLLECTION_MEMBERS = frozenset(
    {"has", "set", "add", "clear", "forEach", "keys", "values", "entries", "size"}
)
_HTTP_MEMBERS = frozenset({"interceptors"})
# `Object.create(proto)` builds a plain object (often a dictionary), not a client.
_NON_CLIENT_FACTORIES = frozenset({"Object.create", "Reflect.create"})
_NAME_TYPES = ("property_identifier", "private_property_identifier")
_WS = re.compile(r"\s+")


def receiver_key(text: str) -> str:
    """The receiver as the dispatcher sees it: ``this . x`` and ``this.x`` are one receiver."""
    return _WS.sub("", text)


def kind_of(evidence: dict[str, int], receiver: str) -> str | None:
    """``HTTP`` / ``COLLECTION`` when the bundle's evidence about ``receiver`` decides it, else
    None (no evidence, or contradictory injected-vs-collection evidence)."""
    flags = evidence.get(receiver_key(receiver), 0)
    if flags & _HTTP_SEEN:
        return HTTP
    if flags & _COLLECTION_SEEN:
        return None if flags & _INJECTED else COLLECTION
    return HTTP if flags & _INJECTED else None


def observe(node: Node, evidence: dict[str, int]) -> None:
    """Record any receiver evidence ``node`` carries. Called once per node of the base-env walk:
    O(1) per node (every decode is a short name or capped by ``_text_if_short``), except a
    function, which reads only its own parameters and top-level statements."""
    kind = node.type
    if kind == "member_expression":
        prop = node.child_by_field_name("property")
        member = _text(prop) if prop is not None and prop.type == "property_identifier" else ""
        if member in _COLLECTION_MEMBERS or member in _HTTP_MEMBERS:
            receiver = _receiver(node.child_by_field_name("object"))
            if receiver:
                flag = _HTTP_SEEN if member in _HTTP_MEMBERS else _COLLECTION_SEEN
                evidence[receiver] = evidence.get(receiver, 0) | flag
    elif kind == "variable_declarator":
        _mark_created(evidence, _receiver(node.child_by_field_name("name")), node, "value")
    elif kind == "assignment_expression":
        _mark_created(evidence, _receiver(node.child_by_field_name("left")), node, "right")
    elif kind == "field_definition":
        prop = node.child_by_field_name("property")
        # Only a plain name: a computed key (`[expr] = 1`) can span the rest of the bundle, and
        # decoding it per nested class would be quadratic. A `static` field is not on `this`.
        if prop is not None and prop.type in _NAME_TYPES and not _is_static(node):
            _mark_created(evidence, "this." + _text(prop), node, "value")
    elif kind == "method_definition":
        name = node.child_by_field_name("name")
        if name is not None and name.type == "property_identifier" and _text(name) == "constructor":
            _mark_injected(node, evidence)
    elif kind in ("function_declaration", "function_expression", "function"):
        _mark_injected(node, evidence)  # ES5 / down-levelled constructor function


def _is_static(field: Node) -> bool:
    return any(child.type == "static" for child in field.children)


def _receiver(node: Node | None) -> str:
    """A plain name or member chain (`x`, `this.x`, `a.b`), else "" — a call, a subscript, or a
    bare `this` (a class's own `this.get(k)` is the class itself, never an injected client)."""
    if node is None or node.type not in ("identifier", "member_expression"):
        return ""
    return receiver_key(_text_if_short(node))


def _mark_created(evidence: dict[str, int], receiver: str, node: Node, value_field: str) -> None:
    value = node.child_by_field_name(value_field)
    if not receiver or value is None:
        return
    flag = 0
    if value.type == "new_expression":
        ctor = value.child_by_field_name("constructor")
        name = _text_if_short(ctor).rsplit(".", 1)[-1] if ctor is not None else ""
        if name in _COLLECTION_CTORS:
            flag = _COLLECTION_SEEN
    elif value.type == "call_expression":
        fn = value.child_by_field_name("function")
        callee = receiver_key(_text_if_short(fn)) if fn is not None else ""
        if callee == "axios.create":
            flag = _HTTP_SEEN
        elif receiver.startswith("this.") and _is_injection_call(callee):
            flag = _INJECTED
    if flag:
        evidence[receiver] = evidence.get(receiver, 0) | flag


def _is_injection_call(callee: str) -> bool:
    """`inject(HttpClient)` / `ɵɵinject(...)` (Angular), or a client factory `ky.create(...)` /
    minified `n.a.create(...)` — provenance only, so it is the weak INJECTED evidence."""
    last = callee.rsplit(".", 1)[-1].rsplit(")", 1)[-1]
    if last.endswith("inject"):
        return True
    return last == "create" and "." in callee and callee not in _NON_CLIENT_FACTORIES


def _mark_injected(function: Node, evidence: dict[str, int]) -> None:
    """``constructor(e, t = null) { this.http = e, this.store = t }``: a field assigned straight
    from a parameter was handed in from outside (dependency injection) — how Angular and
    TS-compiled services receive their HTTP client. Only the function's own parameters and
    top-level statements are read, so the work is bounded by that body, never the whole tree."""
    params = function.child_by_field_name("parameters")
    body = function.child_by_field_name("body")
    if params is None or body is None or body.type != "statement_block":
        return
    param_names: set[str] = set()
    for param in params.named_children:
        if param.type == "assignment_pattern":  # `e = null`
            param = param.child_by_field_name("left") or param
        if param.type == "identifier":
            param_names.add(_text(param))
    if not param_names:
        return
    for statement in body.named_children:
        if statement.type != "expression_statement" or not statement.named_children:
            continue
        pending = [statement.named_children[0]]
        while pending:  # flatten `this.a = e, this.b = t` (a sequence_expression chain)
            expr = pending.pop()
            if expr.type == "sequence_expression":
                pending.extend(expr.named_children)
            elif expr.type == "assignment_expression":
                left, right = expr.child_by_field_name("left"), expr.child_by_field_name("right")
                if right is None or right.type != "identifier" or _text(right) not in param_names:
                    continue
                receiver = _receiver(left)
                if receiver.startswith("this."):
                    evidence[receiver] = evidence.get(receiver, 0) | _INJECTED
