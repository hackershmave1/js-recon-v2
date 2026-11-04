"""HTTP clients the bundle PROVES, under any (minified) name — QA #4.

A minified bundle renames `api` to `wt` and axios to `T`, so a name can say nothing (the #163
principle: decide by what the code does with a value). Two evidence rules, gathered during
:func:`recon.findings._base_env.collect_base_env`'s existing walk (no extra pass, DEBT D21):

- R1 client by configuration: ``X = F.create({ baseURL: ... })`` — ``baseURL`` is the HTTP
  client configuration contract (axios, ofetch, apisauce), so ``X`` is a client, and ``F`` —
  the object that creates clients — is the default client (``T.delete(...)``). Only a plain
  identifier ``F`` is claimed: a webpack ``t.A`` means "export A of whatever module ``t`` is"
  and a sibling module may reuse ``t`` for a store (DEBT D80). The extractor still drops a
  candidate the bundle shows behaving like a collection (Ember's ``cfg.get``/``cfg.set``).
- R2 one-hop wrapper: a function whose RETURNED call is ``fetch(<expr of its params>, ...)``
  (``a => fetch(a)``, ``function c(e,t,n){return fetch(s+e, {method: t||"GET"})}``). Its call
  sites bind the parameters to their arguments (``recon.findings._wrapper_calls``). Only the
  return path is read — never the whole body — so the cost stays linear in the tree, and a
  ``p => xs.map(p => fetch(p))`` shadow is not mistaken for a wrapper.

Bindings the poison set marks as ambiguous (bound more than once) are never used.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from recon.findings._jsast import (
    InferredWrapper,
    _args,
    _object_pairs,
    _string_value,
    _text,
    _text_if_short,
)

_FUNCTION_TYPES = ("arrow_function", "function_expression", "function", "function_declaration")
_NON_CLIENT_FACTORIES = frozenset({"Object", "Reflect"})
_BASE_URL_KEYS = frozenset({"baseURL"})
# A returned call chain (`fetch(a).then(r => r.json()).catch(...)`) is followed this far.
_MAX_CHAIN = 8
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")


def observe(
    node: Node,
    poisoned: set[str],
    instances: dict[str, str | None],
    inferred_instances: set[str],
    wrappers: dict[str, InferredWrapper | None],
) -> None:
    """Record any R1/R2 evidence ``node`` carries. O(1) per node, except a function, which
    reads only its parameters and its return path (top-level statements + a bounded chain)."""
    if node.type == "function_declaration":
        declared = node.child_by_field_name("name")
        if declared is not None and _text(declared) not in poisoned:
            _observe_wrapper(_text(declared), node, wrappers)
        return
    if node.type != "variable_declarator":
        return
    name_node, value = node.child_by_field_name("name"), node.child_by_field_name("value")
    if name_node is None or name_node.type != "identifier" or value is None:
        return
    name = _text(name_node)
    if name in poisoned:
        return
    if value.type == "call_expression":
        _observe_create(name, value, poisoned, instances, inferred_instances)
    elif value.type in _FUNCTION_TYPES:
        _observe_wrapper(name, value, wrappers)
    elif value.type == "object":
        for member in value.named_children:
            if member.type == "pair":
                key, fn = member.child_by_field_name("key"), member.child_by_field_name("value")
                if key is not None and fn is not None and fn.type in _FUNCTION_TYPES:
                    _observe_wrapper(f"{name}.{_key_text(key)}", fn, wrappers)
            elif member.type == "method_definition":
                key = member.child_by_field_name("name")
                if key is not None:
                    _observe_wrapper(f"{name}.{_key_text(key)}", member, wrappers)


def _key_text(key: Node) -> str:
    return (_string_value(key) or "") if key.type == "string" else _text(key)


def _observe_create(
    name: str,
    call: Node,
    poisoned: set[str],
    instances: dict[str, str | None],
    inferred_instances: set[str],
) -> None:
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return
    prop = fn.child_by_field_name("property")
    factory = fn.child_by_field_name("object")
    if prop is None or factory is None or _text(prop) != "create":
        return
    if factory.type not in ("identifier", "member_expression"):
        return
    factory_text = _text_if_short(factory)
    if not factory_text or factory_text.split(".", 1)[0] in _NON_CLIENT_FACTORIES:
        return
    args = _args(call)
    if not args or args[0].type != "object":
        return
    has_base, base = _base_url(args[0])
    if not has_base:
        return
    # setdefault: an axios instance also has `.create`, so `b = a.create(...)` must not erase
    # `a`'s known base (nor may a later sighting overwrite an earlier one).
    instances.setdefault(name, base)
    inferred_instances.add(name)
    # `axios` itself already has its own dispatch branch; only a renamed factory needs claiming.
    if factory.type == "identifier" and factory_text != "axios" and factory_text not in poisoned:
        instances.setdefault(factory_text, None)
        inferred_instances.add(factory_text)


def _base_url(config: Node) -> tuple[bool, str | None]:
    """Whether the config object names a base URL, and its literal value when it has one
    (a shorthand `{ baseURL }` or a computed value is evidence with an unknown base)."""
    for member in config.named_children:
        if member.type == "shorthand_property_identifier" and _text(member) in _BASE_URL_KEYS:
            return True, None
    pairs = _object_pairs(config)
    for key in _BASE_URL_KEYS:
        if key in pairs:
            return True, _string_value(pairs[key])
    return False, None


def _observe_wrapper(key: str, function: Node, wrappers: dict[str, InferredWrapper | None]) -> None:
    params = _param_names(function)
    if not params:
        return
    fetch_call = _returned_fetch(function)
    if fetch_call is None:
        return
    args = _args(fetch_call)
    if not args:
        return
    url = args[0]
    # The URL must read a parameter, or this is just a function that fetches a fixed URL
    # (already an ordinary endpoint) — nothing to bind at the call sites.
    if not set(_IDENTIFIER.findall(_text_if_short(url))) & set(params):
        return
    options = args[1] if len(args) >= 2 and args[1].type == "object" else None
    # Two definitions under one key (two objects reusing a minified name) prove nothing.
    wrappers[key] = None if key in wrappers else InferredWrapper(params, url, options)


def _param_names(function: Node) -> tuple[str, ...]:
    single = function.child_by_field_name("parameter")  # `a => ...`
    if single is not None:
        return (_text(single),) if single.type == "identifier" else ()
    params = function.child_by_field_name("parameters")
    if params is None:
        return ()
    names: list[str] = []
    for param in params.named_children:
        if param.type != "identifier":
            return ()  # a default/destructured/rest param: not a plain substitution
        names.append(_text(param))
    return tuple(names)


def _returned_fetch(function: Node) -> Node | None:
    """The ``fetch(...)`` call a function returns: an arrow's expression body, or the
    expression of a TOP-LEVEL ``return`` — followed down a bounded ``.then().catch()`` chain."""
    body = function.child_by_field_name("body")
    if body is None:
        return None
    expr: Node | None = body
    if body.type == "statement_block":
        returns = [s for s in body.named_children if s.type == "return_statement"]
        expr = (
            returns[0].named_children[0]
            if len(returns) == 1 and returns[0].named_children
            else None
        )
    for _ in range(_MAX_CHAIN):
        if expr is None:
            return None
        if expr.type in ("parenthesized_expression", "await_expression"):
            expr = expr.named_children[0] if expr.named_children else None
            continue
        if expr.type != "call_expression":
            return None
        fn = expr.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return expr if _text(fn) == "fetch" else None
        if fn.type != "member_expression":
            return None
        expr = fn.child_by_field_name("object")  # `fetch(a).then(...)` -> `fetch(a)`
    return None
