"""Name where an unresolved sink's URL comes from in the PAGE (issue #235).

Many real call sites read their URL from the DOM: a Stimulus value (``this.urlValue``), a
jQuery ``attr("data-url")`` / ``data("js-ajax-href")``, ``el.dataset.validationUrl``, a form's
``action``. They are rightly left unresolved (the value lives in the HTML, not the JS), but
``_collapse_url`` renders them as ``EXPR`` / ``:urlValue``, which hides the attribute name a
researcher needs to find the value in the page.

This module returns a DISPLAY token such as ``{attr:data-url}``, ``{data:js-ajax-href}``,
``{stimulus:minify-url}`` or ``{form:action}``. It never feeds the finding's ``value`` or any
hash: the decision on #235 is "show, don't refile", so identity and triage stay exactly as
before and the token rides on the occurrence only.

Bounded by construction (DoS hardening, cf. ``_collapse_url``): the walk only descends
``+``/``||``/``??`` chains, ``.concat()``, parentheses and template substitutions, stops at
``_MAX_DEPTH`` and after ``_MAX_VISITS`` nodes, and only decodes short nodes.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from recon.findings._jsast import _MAX_NODE_TEXT_SPAN, _args, _text, _text_if_short

_MAX_DEPTH = 32  # same cap as `_collapse_url`
_MAX_VISITS = 64  # nodes inspected per URL argument: far beyond a real URL expression
_MAX_TOKENS = 3  # distinct sources shown per occurrence
# An attribute / data key / value name we are willing to echo back. Anything else (a variable,
# a computed name, braces that would break the token) yields no token rather than a guess.
_NAME = re.compile(r"[A-Za-z_][\w:.-]{0,63}")
_ATTR_METHODS = frozenset({"attr", "prop", "getAttribute"})
_CHAIN_OPERATORS = frozenset({"+", "||", "??"})
_UPPER = re.compile(r"[A-Z]")
# Receivers whose `.action` is a form's action: `form`, `searchForm`, `this.form`, an event's
# `target` / `currentTarget`. Anything else (`this.props.action`, a Redux `payload.action`) is a
# JS value, not the page, so it gets no token.
_FORM_RECEIVERS = frozenset({"target", "currenttarget", "srcelement"})
# Native DOM properties that end in `Value` but are not Stimulus values.
_NATIVE_VALUE_PROPS = frozenset({"defaultValue", "nodeValue"})
_MORE = "…"  # marks that more sources existed than `_MAX_TOKENS`


def url_source_token(node: Node | None) -> str | None:
    """The page source(s) of a sink's URL argument, space-separated, or ``None``."""
    if node is None:
        return None
    tokens: list[str] = []
    budget = [_MAX_VISITS]
    _collect(node, 0, budget, tokens)
    if len(tokens) > _MAX_TOKENS:  # never drop sources silently
        tokens = [*tokens[:_MAX_TOKENS], _MORE]
    return " ".join(tokens) if tokens else None


def _collect(node: Node, depth: int, budget: list[int], tokens: list[str]) -> None:
    # One extra token is collected so the caller can tell "exactly 3" from "more than 3".
    if depth >= _MAX_DEPTH or budget[0] <= 0 or len(tokens) > _MAX_TOKENS:
        return
    budget[0] -= 1
    children: list[Node | None] = []
    if node.type == "parenthesized_expression":
        children = list(node.named_children)
    elif node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is not None and _text(operator) in _CHAIN_OPERATORS:
            children = [node.child_by_field_name("left"), node.child_by_field_name("right")]
    elif node.type == "template_string":
        children = [c.named_children[0] for c in node.named_children if c.named_children]
    elif node.type == "call_expression" and _is_concat(node):
        fn = node.child_by_field_name("function")
        children = [fn.child_by_field_name("object") if fn else None, *_args(node)]
    else:
        token = _leaf_token(node)
        if token is not None and token not in tokens:
            tokens.append(token)
        return
    for child in children:
        if child is not None:
            _collect(child, depth + 1, budget, tokens)


def _is_concat(call: Node) -> bool:
    fn = call.child_by_field_name("function")
    return (
        fn is not None
        and fn.type == "member_expression"
        and _text_if_short(fn.child_by_field_name("property")) == "concat"
    )


def _leaf_token(node: Node) -> str | None:
    if node.type == "call_expression":
        return _call_token(node)
    if node.type == "member_expression":
        return _member_token(node)
    if node.type == "subscript_expression":
        return _subscript_token(node)
    return None


def _call_token(call: Node) -> str | None:
    """``x.attr("data-url")`` / ``x.prop(..)`` / ``x.getAttribute(..)`` -> ``{attr:..}``;
    ``x.data("key")`` -> ``{data:key}``; Stimulus 1 ``this.data.get("url")`` -> ``{stimulus:url}``."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    method = _text_if_short(fn.child_by_field_name("property"))
    args = _args(call)
    name = _literal_name(args[0]) if args else None
    if name is None:
        return None
    if method in _ATTR_METHODS:
        return "{attr:" + name + "}"
    if method == "data":  # jQuery maps `data("ajaxUrl")` and `data("ajax-url")` to one attribute
        return "{data:" + _kebab(name) + "}"
    if method == "get" and _text_if_short(fn.child_by_field_name("object")) == "this.data":
        return "{stimulus:" + name + "}"
    return None


def _member_token(node: Node) -> str | None:
    """``this.minifyUrlValue`` -> ``{stimulus:minify-url}``; ``el.dataset.validationUrl`` ->
    ``{data:validation-url}``; ``form.action`` / ``e.target.action`` -> ``{form:action}``.

    A bare ``.href`` gets NO token: ``url.href``, ``new URL(..).href`` and HAL
    ``_links.next.href`` hold JS / API-response values, not markup, so naming an attribute there
    would send the researcher to search the DOM for something that isn't in it."""
    prop = _text_if_short(node.child_by_field_name("property"))
    obj = node.child_by_field_name("object")
    if obj is None or not _NAME.fullmatch(prop):
        return None
    if (
        obj.type == "this"
        and prop.endswith("Value")
        and len(prop) > len("Value")
        and prop not in _NATIVE_VALUE_PROPS
    ):
        return "{stimulus:" + _kebab(prop[: -len("Value")]) + "}"
    receiver = _last_segment(obj)
    if receiver == "dataset":
        return "{data:" + _kebab(prop) + "}"
    if prop == "action" and _is_form_receiver(receiver):
        return "{form:action}"
    return None


def _is_form_receiver(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith("form") or lowered in _FORM_RECEIVERS


def _subscript_token(node: Node) -> str | None:
    """``el.dataset["validationUrl"]`` -> ``{data:validation-url}``."""
    obj = node.child_by_field_name("object")
    name = _literal_name(node.child_by_field_name("index"))
    if obj is None or name is None or _last_segment(obj) != "dataset":
        return None
    return "{data:" + _kebab(name) + "}"


def _literal_name(node: Node | None) -> str | None:
    if node is None or node.type != "string":
        return None
    if node.end_byte - node.start_byte > _MAX_NODE_TEXT_SPAN:
        return None
    name = _text(node)[1:-1]
    return name if _NAME.fullmatch(name) else None


def _last_segment(node: Node) -> str:
    if node.type == "member_expression":
        return _text_if_short(node.child_by_field_name("property"))
    return _text_if_short(node) if node.type == "identifier" else ""


def _kebab(camel: str) -> str:
    """The HTML attribute spelling of a DOM ``dataset`` / Stimulus value key — the spec's own
    camelCase -> kebab-case mapping (a ``-`` before EVERY capital: ``apiURL`` -> ``api-u-r-l``, as
    the DOM and Stimulus do), so the token names what is literally in the markup."""
    return _UPPER.sub(lambda match: "-" + match.group().lower(), camel)
