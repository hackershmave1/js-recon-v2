"""Evaluate a call to an INFERRED one-hop wrapper (recon.findings._inferred, QA #4).

``c("/orders/" + id + "/cancel", "POST")`` against ``function c(e,t,n){return fetch(s+e,
{method:t||"GET", body:n?JSON.stringify(n):void 0})}``: bind the wrapper's parameters to the
call's argument nodes, then evaluate the wrapper's own URL / method / body expressions with
that substitution. A parameter resolves only through its argument (statically, via the normal
URL resolver), a member of a parameter (`p.url`) only through that argument object's key, and
anything else only as a literal or a known const — never guessed (REQ-C2). An unresolvable
URL becomes an unresolved row attributed to the wrapper, exactly like any other sink.
"""

from __future__ import annotations

from dataclasses import replace

from tree_sitter import Node

from recon.findings._base_env import _resolve_url
from recon.findings._jsast import (
    HTTP_METHODS,
    BaseEnv,
    Extraction,
    InferredWrapper,
    _args,
    _body_params_from_value,
    _endpoint,
    _object_pairs,
    _query_params,
    _string_value,
    _text,
)

_MAX_DEPTH = 16
Substitution = dict[str, Node | None]


def record_wrapper_call(
    call: Node, key: str, wrapper: InferredWrapper, result: Extraction, env: BaseEnv
) -> None:
    # Imported here: extract imports this module, and _record_unresolved lives in extract.
    from recon.findings.extract import _record_unresolved

    args = _args(call)
    subs: Substitution = {
        name: (args[i] if i < len(args) else None) for i, name in enumerate(wrapper.params)
    }
    options = _object_pairs(wrapper.options) if wrapper.options is not None else {}
    method = (_eval_method(options.get("method"), subs) or "GET").upper()
    if method not in HTTP_METHODS:
        method = "GET"
    url = _eval_url(wrapper.url, subs, env, 0)
    if url is None:
        _record_unresolved(result, "fetch", method, args[0] if args else None, call)
        result.unresolved[-1] = replace(result.unresolved[-1], inferred_wrapper=key)
        return
    params = _query_params(url) + _body_params_from_value(_body_target(options.get("body"), subs))
    endpoint = _endpoint("fetch", method, url, params, call, mark=args[0] if args else None)
    result.endpoints.append(replace(endpoint, inferred_wrapper=key))


def _argument(node: Node, subs: Substitution) -> tuple[bool, Node | None]:
    """(is a parameter reference, the bound node): ``p`` -> its argument, ``p.k`` -> that
    argument object's ``k`` value."""
    if node.type == "identifier" and _text(node) in subs:
        return True, subs[_text(node)]
    if node.type == "member_expression":
        obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
        if obj is not None and prop is not None and obj.type == "identifier" and _text(obj) in subs:
            bound = subs[_text(obj)]
            if bound is None or bound.type != "object":
                return True, None
            return True, _object_pairs(bound).get(_text(prop))
    return False, None


def _eval_url(node: Node | None, subs: Substitution, env: BaseEnv, depth: int) -> str | None:
    if node is None or depth > _MAX_DEPTH:
        return None
    is_param, bound = _argument(node, subs)
    if is_param:
        return _resolve_url(bound, env, "") if bound is not None else None
    if node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is None or _text(operator) != "+":
            return None
        left = _eval_url(node.child_by_field_name("left"), subs, env, depth + 1)
        right = _eval_url(node.child_by_field_name("right"), subs, env, depth + 1)
        return left + right if left is not None and right is not None else None
    if node.type == "parenthesized_expression" and len(node.named_children) == 1:
        return _eval_url(node.named_children[0], subs, env, depth + 1)
    if node.type == "template_string":
        parts: list[str] = []
        for child in node.children:
            if child.type == "template_substitution":
                inner = child.named_children[0] if child.named_children else None
                value = _eval_url(inner, subs, env, depth + 1)
                if value is None:
                    return None
                parts.append(value)
            elif child.type != "`":
                parts.append(_text(child))
        return "".join(parts)
    if node.type == "identifier" or node.type == "member_expression":
        return _resolve_url(node, env, "")  # a known const only
    return _string_value(node)


def _eval_method(node: Node | None, subs: Substitution) -> str | None:
    if node is None:
        return None
    is_param, bound = _argument(node, subs)
    if is_param:
        return _string_value(bound) if bound is not None else None
    if node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is not None and _text(operator) in ("||", "??"):
            return _eval_method(node.child_by_field_name("left"), subs) or _eval_method(
                node.child_by_field_name("right"), subs
            )
        return None
    return _string_value(node)


def _body_target(node: Node | None, subs: Substitution) -> Node | None:
    """The call-site node the wrapper's ``body`` serializes: through ``cond ? X : void 0`` and
    ``JSON.stringify(X)``, then ``X`` bound to its argument (``n`` / ``a.data``)."""
    for _ in range(4):
        if node is None:
            return None
        if node.type == "ternary_expression":
            node = node.child_by_field_name("consequence")
        elif node.type == "call_expression" and _text(node.child_by_field_name("function")) == (
            "JSON.stringify"
        ):
            args = _args(node)
            node = args[0] if args else None
        else:
            break
    if node is None:
        return None
    is_param, bound = _argument(node, subs)
    return bound if is_param else None
