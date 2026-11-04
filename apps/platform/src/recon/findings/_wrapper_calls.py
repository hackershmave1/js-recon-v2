"""Evaluate a call to an INFERRED one-hop wrapper (recon.findings._inferred, QA #4).

``c("/orders/" + id + "/cancel", "POST")`` against ``function c(e,t,n){return fetch(s+e,
{method:t||"GET", body:n?JSON.stringify(n):void 0})}``: bind the wrapper's parameters to the
call's argument nodes, then evaluate the wrapper's own URL / options with that substitution.
A parameter resolves only through its argument (statically, via the normal URL resolver), a
member of a parameter (`p.url`) only through that argument object's key, and anything else
only as a literal or a known const — never guessed (REQ-C2). Options the wrapper takes as a
parameter (`fetch(u, o)`), spreads (`{...o}`) or names by shorthand (`{method}`) are read from
the call site. Whatever cannot be evaluated makes the call an unresolved row attributed to the
wrapper — never a confirmed endpoint with a defaulted method or a wrong URL.
"""

from __future__ import annotations

from dataclasses import replace

from tree_sitter import Node

from recon.findings._base_env import _resolve_url
from recon.findings._inferred import identifiers_of
from recon.findings._jsast import (
    HTTP_METHODS,
    BaseEnv,
    Extraction,
    InferredWrapper,
    _args,
    _auth_headers,
    _body_params_from_value,
    _collapse_url,
    _endpoint,
    _object_pairs,
    _query_params,
    _string_value,
    _text,
)

_MAX_DEPTH = 16
Substitution = dict[str, Node | None]
# An option value and the substitution it is evaluated under: a wrapper-side expression reads
# the wrapper's params; a call-side value is already the caller's own expression (no subs).
OptionValue = tuple[Node, Substitution]


def record_wrapper_call(
    call: Node, key: str, wrapper: InferredWrapper, result: Extraction, env: BaseEnv
) -> None:
    args = _args(call)
    subs: Substitution = {
        name: (args[i] if i < len(args) else None) for i, name in enumerate(wrapper.params)
    }
    mark = _url_argument(wrapper, subs)
    options = _options(wrapper.options, subs)
    method = _method(options)
    url = _eval_url(wrapper.url, subs, env, 0) if options is not None else None
    if url is None or method is None:
        # Not provable statically: an honest unresolved row, its skeleton built from the
        # wrapper's URL expression with the caller's argument in place (`https://a.io:dyn`).
        result.unattributed += 1
        skeleton = _skeleton(wrapper.url, subs, env, 0)
        row = _endpoint("fetch", method or "GET", skeleton, [], call, mark=mark)
        result.unresolved.append(replace(row, inferred_wrapper=key))
        return
    assert options is not None
    params = _query_params(url)
    body = options.get("body")
    if body is not None:
        params += _body_params_from_value(_body_target(*body))
    headers = options.get("headers")
    endpoint = _endpoint(
        "fetch",
        method,
        url,
        params,
        call,
        headers=_auth_headers(headers[0]) if headers is not None else [],
        mark=mark,
    )
    result.endpoints.append(replace(endpoint, inferred_wrapper=key))


def _url_argument(wrapper: InferredWrapper, subs: Substitution) -> Node | None:
    """The call's argument the wrapper's URL reads (for the location mark), not just args[0]."""
    read = identifiers_of(wrapper.url)
    for name in wrapper.params:
        if name in read and subs.get(name) is not None:
            return subs[name]
    return None


def _argument(node: Node, subs: Substitution) -> tuple[bool, Node | None]:
    """(is a parameter reference, the bound node): ``p`` / shorthand ``{p}`` -> its argument,
    ``p.k`` -> that argument object's ``k`` value."""
    if node.type in ("identifier", "shorthand_property_identifier") and _text(node) in subs:
        return True, subs[_text(node)]
    if node.type == "member_expression":
        obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
        if obj is not None and prop is not None and obj.type == "identifier" and _text(obj) in subs:
            bound = subs[_text(obj)]
            if bound is None or bound.type != "object":
                return True, None
            return True, _object_pairs(bound).get(_text(prop))
    return False, None


def _options(node: Node | None, subs: Substitution) -> dict[str, OptionValue] | None:
    """The effective fetch options as key -> (value, its substitution), or ``None`` when they
    cannot be known (a parameter bound to a non-literal, a spread of something unknown)."""
    if node is None:
        return {}
    is_param, bound = _argument(node, subs)
    if is_param:
        return _call_side(bound)
    if node.type != "object":
        return None
    options: dict[str, OptionValue] = {}
    for member in node.named_children:
        if member.type == "pair":
            key, value = member.child_by_field_name("key"), member.child_by_field_name("value")
            if key is not None and value is not None:
                options[_string_value(key) or _text(key)] = (value, subs)
        elif member.type == "shorthand_property_identifier":
            options[_text(member)] = (member, subs)
        elif member.type == "spread_element":
            inner = member.named_children[0] if member.named_children else None
            spread_param, spread_bound = (
                _argument(inner, subs) if inner is not None else (False, None)
            )
            if not spread_param:
                return None
            spread = _call_side(spread_bound)
            if spread is None:
                return None
            options.update(spread)
    return options


def _call_side(bound: Node | None) -> dict[str, OptionValue] | None:
    if bound is None:
        return {}  # the caller passed no options
    if bound.type != "object":
        return None
    return {key: (value, {}) for key, value in _object_pairs(bound).items()}


def _method(options: dict[str, OptionValue] | None) -> str | None:
    if options is None:
        return None
    if "method" not in options:
        return "GET"
    node, subs = options["method"]
    method = _eval_method(node, subs)
    if method is None:
        return None  # a method we can't read: never default a confirmed endpoint to GET
    upper = method.upper()
    return upper if upper in HTTP_METHODS else None


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
    if node.type in ("identifier", "member_expression"):
        return _resolve_url(node, env, "")  # a known const only
    return _string_value(node)


def _skeleton(node: Node | None, subs: Substitution, env: BaseEnv, depth: int) -> str:
    """Best-effort display URL for an unresolved wrapper call: resolvable parts resolved, the
    rest collapsed (`:dyn`), the caller's argument standing in for the parameter."""
    if node is None or depth > _MAX_DEPTH:
        return _collapse_url(None)
    resolved = _eval_url(node, subs, env, depth)
    if resolved is not None:
        return resolved
    is_param, bound = _argument(node, subs)
    if is_param:
        return _collapse_url(bound)
    if node.type == "binary_expression":
        left = _skeleton(node.child_by_field_name("left"), subs, env, depth + 1)
        return left + _skeleton(node.child_by_field_name("right"), subs, env, depth + 1)
    return _collapse_url(node)


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
    """The call-site node the ``body`` serializes: through ``cond ? X : void 0`` and
    ``JSON.stringify(X)``, then ``X`` bound to its argument (``n`` / ``a.data``); a literal
    object stays itself."""
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
    return bound if is_param else node
