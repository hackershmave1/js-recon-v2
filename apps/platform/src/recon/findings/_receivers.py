"""What a `.get()`/`.post()` receiver IS, decided by what the bundle itself says about it.

A call like ``this.idNodeMap.get(e)`` is shaped exactly like ``this.http.get(url)``; a receiver's
NAME cannot tell them apart (that guess counted 61 Map/Set lookups in one real capture as
unresolved HTTP calls). The code can: the same bundle almost always shows how a receiver was
created and what else is done with it. Evidence is collected per receiver text (``this.X`` or a
local name) during :func:`recon.findings._base_env.collect_base_env`'s existing walk — no extra
tree pass (DEBT D21):

- COLLECTION: created as ``new Map/Set/WeakMap/...`` (assignment, declaration or class field), or
  used with a member no HTTP client has (``.has/.set/.add/.clear/.forEach/.keys/...``).
- HTTP: injected through a constructor parameter (``constructor(e){this.http=e}`` — Angular/TS
  DI), created by ``axios.create(...)``, or used with an HTTP-client-only member
  (``.interceptors/.defaults/.request``).

Evidence is ranked, strongest kept: what an object is created as or used for (axios.create,
.interceptors / new Map, .has) outranks where it came from. Injection is the weakest: an injected
cache that is later ``.set`` is a collection, but an injected field with no other evidence is the
DI-delivered client. A receiver with no evidence is neither: its call is only SUSPECTED.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from recon.findings._jsast import _text, _text_if_short

COLLECTION = "collection"
HTTP = "http"

# Evidence strength (higher wins): created as / used as an HTTP client > used or created as a
# collection > handed in through a constructor (provenance only).
_INJECTED, _COLLECTION_RANK, _HTTP_RANK = 1, 2, 3

_COLLECTION_CTORS = frozenset(
    {"Map", "Set", "WeakMap", "WeakSet", "Array", "URLSearchParams", "Headers", "FormData"}
)
# Members only a collection has (not `get`/`delete`: those are the HTTP verbs being judged).
_COLLECTION_MEMBERS = frozenset(
    {"has", "set", "add", "clear", "forEach", "keys", "values", "entries", "size"}
)
_HTTP_MEMBERS = frozenset({"interceptors", "defaults", "request"})
_WS = re.compile(r"\s+")


def receiver_key(text: str) -> str:
    """The receiver as the dispatcher sees it: ``this . x`` and ``this.x`` are one receiver."""
    return _WS.sub("", text)


def kind_of(evidence: dict[str, int], receiver: str) -> str | None:
    """``HTTP`` / ``COLLECTION`` for a receiver the bundle gives evidence about, else None."""
    rank = evidence.get(receiver_key(receiver))
    if rank is None:
        return None
    return COLLECTION if rank == _COLLECTION_RANK else HTTP


def observe(node: Node, evidence: dict[str, int]) -> None:
    """Record any receiver evidence ``node`` carries. Called once per node of the base-env walk;
    O(1) per node except a constructor, which reads only its own direct statements."""
    kind = node.type
    if kind == "member_expression":
        prop = node.child_by_field_name("property")
        member = _text(prop) if prop is not None and prop.type == "property_identifier" else ""
        if member in _COLLECTION_MEMBERS or member in _HTTP_MEMBERS:
            receiver = _receiver(node.child_by_field_name("object"))
            if receiver:
                _mark(
                    evidence, receiver, _HTTP_RANK if member in _HTTP_MEMBERS else _COLLECTION_RANK
                )
    elif kind == "variable_declarator":
        _mark_created(evidence, _receiver(node.child_by_field_name("name")), node, "value")
    elif kind == "assignment_expression":
        _mark_created(evidence, _receiver(node.child_by_field_name("left")), node, "right")
    elif kind == "field_definition":
        prop = node.child_by_field_name("property")
        if prop is not None:
            _mark_created(evidence, "this." + _text(prop), node, "value")
    elif kind == "method_definition":
        _mark_injected(node, evidence)


def _receiver(node: Node | None) -> str:
    """A plain name or member chain (`x`, `this.x`, `a.b`), else "" (calls, subscripts...)."""
    if node is None or node.type not in ("identifier", "member_expression", "this"):
        return ""
    return receiver_key(_text_if_short(node))


def _mark(evidence: dict[str, int], receiver: str, rank: int) -> None:
    if rank > evidence.get(receiver, 0):  # keep the strongest evidence seen
        evidence[receiver] = rank


def _mark_created(evidence: dict[str, int], receiver: str, node: Node, value_field: str) -> None:
    value = node.child_by_field_name(value_field)
    if not receiver or value is None:
        return
    if value.type == "new_expression":
        ctor = value.child_by_field_name("constructor")
        name = _text_if_short(ctor).rsplit(".", 1)[-1] if ctor is not None else ""
        if name in _COLLECTION_CTORS:
            _mark(evidence, receiver, _COLLECTION_RANK)
    elif value.type == "call_expression" and _is_axios_create(value):
        _mark(evidence, receiver, _HTTP_RANK)


def _is_axios_create(call: Node) -> bool:
    fn = call.child_by_field_name("function")
    return fn is not None and receiver_key(_text_if_short(fn)) == "axios.create"


def _mark_injected(method: Node, evidence: dict[str, int]) -> None:
    """``constructor(e, t) { this.http = e, this.store = t }``: a field assigned straight from a
    constructor parameter was handed in from outside (dependency injection) — how Angular and
    TS-compiled services receive their HTTP client. Only the constructor's own top-level
    statements are read, so the work is bounded by that body, never the whole tree."""
    name = method.child_by_field_name("name")
    params = method.child_by_field_name("parameters")
    body = method.child_by_field_name("body")
    if name is None or _text(name) != "constructor" or params is None or body is None:
        return
    param_names = {_text(p) for p in params.named_children if p.type == "identifier"}
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
                if (
                    left is not None
                    and right is not None
                    and right.type == "identifier"
                    and _text(right) in param_names
                    and _receiver(left).startswith("this.")
                ):
                    _mark(evidence, _receiver(left), _INJECTED)
