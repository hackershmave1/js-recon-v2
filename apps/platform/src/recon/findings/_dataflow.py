"""Single-unshadowed local constant resolution for the JS extractor (Phase 2, Option B).

:func:`resolve_local_consts` maps each LOCAL constant name to its resolved string value — the
recovery lever that lets a sink whose URL is *held in* or *built from* a local ``const`` resolve
instead of landing ``unattributed``. It covers a bare literal (``const u = "…"``) AND a value
BUILT from other local consts / literals (``const url = base + "/orders"``), so ``fetch(u)`` /
``fetch(url)`` / ``fetch(base + "/x")`` all fold at the sink (via ``_base_env._resolve_url``
reading ``BaseEnv.const_prefixes``).

Honesty (REQ-C2) rests ENTIRELY on the poison set: the caller (``_base_env.collect_base_env``)
passes only names bound EXACTLY once — a name bound / reassigned / shadowed more than once
anywhere in the file (``_declared_names``) is excluded — so a folded value cannot differ at the
sink, making the fold certain rather than a guess. All-or-none: a binding whose value references
an unresolvable operand (a call result, a member access, a name not in the map, a ternary) is
omitted, never half-guessed.

This module does NOT walk the tree: the caller already collects the candidate ``name -> value``
bindings inside its single ``collect_base_env`` walk and hands them here, so Phase 2 adds no new
full-tree pass (the base-env walks are the extractor's unbounded — for soundness — worst case;
see DEBT D21). Resolution is memoized and cycle-/depth-capped, and the result is a plain dict, so
the sink resolver stays an O(1) lookup — no per-sink re-traversal (the O(n^2) class forbidden here).

Leaf position in the import DAG: ``_jsast`` <- ``_dataflow`` <- ``_base_env`` <- ``extract``.
"""

from __future__ import annotations

from tree_sitter import Node

from recon.findings._jsast import _string_value, _text

# A binding whose value is a `+` chain (or reference chain) deeper than this is left unresolved
# rather than recursed into — bounds a crafted `const a = "x" + "y" + …` / `const a = b; const
# b = c; …` chain (the string-splitting evasion this product targets) so resolution cannot blow
# the Python stack. Cycles are broken by `active` below; this only bounds acyclic depth. Mirrors
# `_base_env._MAX_CONCAT_DEPTH`.
_MAX_RESOLVE_DEPTH = 40


def resolve_local_consts(raw: dict[str, Node]) -> dict[str, str]:
    """Resolve pre-collected single-unshadowed local bindings to their string values.

    ``raw`` maps a local constant name to its value node — collected by the caller's own
    ``collect_base_env`` walk, already filtered to names bound exactly once (the 0-FP guarantee)
    and excluding axios-instance bindings. Returns only the names that resolve to a fully-static
    string; a binding whose value is a call result, a member access, a reference to a name not in
    ``raw``, or a ternary is omitted, so a sink reading the result never gets a guess.
    """
    resolved: dict[str, str] = {}

    def resolve_name(name: str, active: frozenset[str]) -> str | None:
        if name in resolved:
            return resolved[name]
        if name not in raw or name in active:
            return None  # not a local const, or a reference cycle -> unresolvable (honest)
        value = _resolve_node(raw[name], active | {name}, 0)
        if value is not None:
            resolved[name] = value
        return value

    def _resolve_node(node: Node | None, active: frozenset[str], depth: int) -> str | None:
        if node is None or depth > _MAX_RESOLVE_DEPTH:
            return None
        literal = _string_value(node)  # string / substitution-preserving template literal
        if literal is not None:
            return literal
        if node.type == "identifier":
            return resolve_name(_text(node), active)
        if node.type == "parenthesized_expression":
            inner = node.named_children
            return _resolve_node(inner[0], active, depth + 1) if len(inner) == 1 else None
        if node.type == "binary_expression":
            operator = node.child_by_field_name("operator")
            if operator is None or _text(operator) != "+":
                return None  # only string `+` concatenation folds
            left = _resolve_node(node.child_by_field_name("left"), active, depth + 1)
            if left is None:
                return None
            right = _resolve_node(node.child_by_field_name("right"), active, depth + 1)
            if right is None:
                return None
            return left + right  # pure concatenation (mirrors _base_env._resolve_concat_operand)
        return None

    for name in raw:
        resolve_name(name, frozenset())
    return resolved
