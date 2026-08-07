"""Base-URL resolution for the JS extractor (REQ-C2; DEBT D11 middle layer).

The scope-safe base-environment pre-pass (:func:`collect_base_env`) plus the
sink-time URL resolver (:func:`_resolve_url`) that folds a leading ``${CONST}``
prefix and joins an axios-instance / ``axios.defaults`` base onto a relative
path. Imports its AST primitives from :mod:`recon.findings._jsast` (the leaf)
and is itself imported by :mod:`recon.findings.extract` (the sink handlers).
"""

from __future__ import annotations

import re

from tree_sitter import Node

from recon.findings._jsast import (
    BaseEnv,
    _args,
    _object_pairs,
    _string_value,
    _text,
    _walk,
)

# --- base-environment collection (scope-safe pre-pass; Task 1) ---------------
#
# A pure, read-only pass that records only statically-certain, unshadowed
# base-URL bindings (spec REQ §3.1/§3.3 gate B1) so a later pass (Task 2)
# resolves `instance.get(...)` calls back to a full URL. Honesty over guessing,
# same principle as REQ-C2 above: a name that is ambiguous anywhere in the
# file — redeclared, shadowed by a parameter, or reassigned — is EXCLUDED
# rather than resolved to a possibly-wrong base. Wired into `extract()` and
# the sink handlers in `recon.findings.extract` by "URL resolution at the sink (Task 2)".


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
    return (
        fn is not None
        and fn.type == "member_expression"
        and _text(fn.child_by_field_name("object")) == "axios"
        and _text(fn.child_by_field_name("property")) == "create"
    )


def _base_url_arg(create_call: Node) -> str | None:
    args = _args(create_call)
    if args and args[0].type == "object":
        return _string_value(_object_pairs(args[0]).get("baseURL"))
    return None


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
    remainder = body[len(leading) :]
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
