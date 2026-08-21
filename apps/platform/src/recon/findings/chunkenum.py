"""Static webpack lazy-chunk URL enumeration (P4, NO execution).

Webpack splits an app into lazy-loaded chunks whose URLs are built at runtime by
``__webpack_require__.u(chunkId)`` (e.g. ``id => "static/chunks/" + id + "." +
{100:"a1b2"}[id] + ".js"``). Those chunks hide additional API calls, but neither a
static pass nor katana sees them because the URL never appears as a literal.

This module recovers those URLs *statically*: it reads the ``.u`` builder + the
chunk-id set from the parsed bundle and folds the builder template per id by pure
string substitution — it never executes anything. It covers the standard template
shapes; an unrecognised / computed builder enumerates NOTHING (fail-safe, never an
invented URL), and an id with no static hash entry is skipped rather than guessed.

Executing *arbitrary/obfuscated* builders (the recall edge that needs a real JS
sandbox) is the deferred follow-on — a Node-under-nsjail engine gated on the six
§4 security must-fixes (DEBT D29). This static path is its input and ships first.

Honesty (REQ-C2), same rule as the rest of the extractor: only statically-certain
string literals cross into a URL; anything dynamic is absent, never guessed. The
enumerated URLs are content-derived and therefore UNTRUSTED — the caller must route
every one through ``fetch.egress.validate_target`` (they can never widen scope) and
cap the count/length before seeding (the ceiling args here bound both).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from recon.findings._jsast import (
    _MAX_URL_SPAN,
    _PARSER,
    _args,
    _object_pairs,
    _string_value,
    _text,
    _text_if_short,
    _walk,
)

# Default ceilings. The inline chunk map is attacker-controlled content, so both the
# URL count and per-URL length are bounded here (the analyze caller passes the run's
# crawl asset cap). §4 finding 4: cap before the URLs reach the fetch queue.
_DEFAULT_MAX_URLS = 512
_DEFAULT_MAX_URL_LEN = 2048

# Recursion bound for the +-chain fold. A real chunk-URL template is a handful of operands;
# a pathologically deep ``"a"+"a"+…`` chain (a static-analysis-evasion shape) must degrade to
# "non-foldable" rather than raise RecursionError — so this module keeps its "never raises /
# non-foldable -> nothing" contract independent of any caller's try/except. Well under CPython's
# ~1000 default and far beyond any real template. (DoS / contract hardening.)
_MAX_FOLD_DEPTH = 64

# Max arms to walk in a conditional (ternary / if-chain) `.u` builder. Real builders have
# tens-to-low-hundreds of arms; a pathological chain is bounded here. The chain walk is
# iterative, so recursion depth is unaffected by arm count.
_MAX_COND_ARMS = 8192

# Body-span cap for a conditional `.u` builder. Unlike the template builder (a small
# template reused per id), a ternary/if-chain builder is one arm PER lazy chunk, so its
# body is legitimately large (Asana's is ~13 KB) — the small `_MAX_URL_SPAN` template cap
# would reject it. The walk is O(body) once and arm-capped, so a generous 1 MiB bound is
# safe while still rejecting a truly pathological builder.
_MAX_COND_BODY_SPAN = 1_048_576


@dataclass(frozen=True)
class _Part:
    """One folded segment of the ``.u`` template: a string literal, the chunk-id
    parameter, or a ``{id: hash}`` lookup keyed by that parameter."""

    kind: str  # "lit" | "param" | "hashmap"
    literal: str = ""
    hashmap: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Builder:
    parts: tuple[_Part, ...]
    param: str  # the builder's single parameter name (the chunk id)
    obj: str  # the require-alias identifier text (scopes .e()/.p to this bundle)
    hashmap_keys: tuple[str, ...]  # chunk ids discoverable from the map itself


def enumerate_chunk_urls(
    source: str,
    *,
    max_urls: int = _DEFAULT_MAX_URLS,
    max_url_len: int = _DEFAULT_MAX_URL_LEN,
) -> list[str]:
    """Statically enumerate a webpack bundle's lazy-chunk URLs from ``source``.

    Returns a de-duplicated list of URL strings (relative to the bundle when the
    public path is dynamic, absolute when it is a literal). Empty when ``source`` is
    not a recognisable webpack runtime or the builder can't be folded statically —
    self-gating, so it is safe to call on every asset.
    """
    root = _PARSER.parse(source.encode("utf-8")).root_node
    builder = _find_u_builder(root)
    if builder is None:
        # No template-form builder — try the conditional form real Next.js/webpack emit:
        # a `.u` written as a ternary chain `id===e?"url":…` or a block-body if-chain
        # `(e)=>{if(id===e)return"url";…}`. Same fail-safe contract (unrecognised → nothing).
        return _enumerate_conditional(root, max_urls=max_urls, max_url_len=max_url_len)

    prefix = _find_public_path(root, builder.obj)
    parts = (_Part("lit", literal=prefix), *builder.parts) if prefix else builder.parts

    urls: list[str] = []
    seen: set[str] = set()
    for chunk_id in _collect_chunk_ids(root, builder):
        if len(urls) >= max_urls:
            break
        url = _fold(parts, chunk_id)
        if url is None or len(url) > max_url_len or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _find_u_builder(root: Node) -> _Builder | None:
    """Locate ``<alias>.u = <fn>`` and fold the builder body into template parts, or
    ``None`` if absent / not statically foldable."""
    for node in _walk(root):
        if node.type != "assignment_expression":
            continue
        left = node.child_by_field_name("left")
        if left is None or left.type != "member_expression":
            continue
        if _text_if_short(left.child_by_field_name("property")) != "u":
            continue
        obj = _text_if_short(left.child_by_field_name("object"))
        if not obj:
            continue
        fn = _as_builder_fn(node.child_by_field_name("right"))
        if fn is None:
            continue
        param, body = fn
        if body.end_byte - body.start_byte > _MAX_URL_SPAN:
            continue  # oversized body -> not statically foldable (DoS bound)
        folded = _fold_body(body, param)
        if folded is None:
            continue
        parts, keys = folded
        return _Builder(parts=tuple(parts), param=param, obj=obj, hashmap_keys=tuple(keys))
    return None


def _as_builder_fn(node: Node | None) -> tuple[str, Node] | None:
    """A single-parameter function's ``(param_name, body_expression)``, or ``None``.

    Handles the arrow expression body (``e => …``), the arrow block body
    (``e => { return … }``), and the ``function (e) { return … }`` form."""
    if node is None or node.type not in ("arrow_function", "function_expression", "function"):
        return None
    param = _single_param(node)
    if param is None:
        return None
    body = node.child_by_field_name("body")
    if body is None:
        return None
    if body.type == "statement_block":
        # Collapse to the return ONLY when the block is exactly `{ return <expr>; }` — one named
        # child that is a `return_statement`. ANY other block (an `if`/`switch`/`try`/nested or
        # labeled block, a temp assignment before the return, …) BRANCHES or computes, so its
        # trailing `return "<template>"` is a DEFAULT/partial arm, not THE url: folding it against
        # real `.e(id)` sites would apply one arm's format to every id, inventing a URL for ids a
        # branch actually matches (a "guessed URL", §4 honesty violation). Refuse the collapse so
        # `enumerate_chunk_urls` routes to `_enumerate_conditional`, which folds only recognised
        # `id===` arms (block if-chain / ternary) and drops everything else — honest, never
        # invented. Keying on the single-return SHAPE (not on `if_statement` specifically) closes
        # the whole branching-wrapper class, not just the flat if-chain (§4 gate-2 follow-up).
        named = body.named_children
        if len(named) != 1 or named[0].type != "return_statement":
            return None
        body = _return_expr(body)
    return (param, body) if body is not None else None


def _single_param(fn: Node) -> str | None:
    """The name of the function's ONE identifier parameter, else ``None`` (a chunk-URL
    builder takes exactly the chunk id)."""
    params = fn.child_by_field_name("parameters")
    if params is not None:
        idents = [c for c in params.named_children if c.type == "identifier"]
        return _text_if_short(idents[0]) if len(idents) == 1 else None
    single = fn.child_by_field_name("parameter")
    if single is not None and single.type == "identifier":
        return _text_if_short(single)
    return None


def _return_expr(block: Node) -> Node | None:
    for child in block.named_children:
        if child.type == "return_statement":
            returned = child.named_children
            return returned[0] if returned else None
    return None


def _fold_body(node: Node, param: str) -> tuple[list[_Part], list[str]] | None:
    """Fold a ``+``-concatenation of literals / the param / a ``{map}[param]`` lookup
    into ordered parts. ``None`` if any operand is not one of those (fail-safe)."""
    parts: list[_Part] = []
    keys: list[str] = []
    if not _fold_into(node, param, parts, keys):
        return None
    return parts, keys


def _fold_into(
    node: Node | None, param: str, parts: list[_Part], keys: list[str], _depth: int = 0
) -> bool:
    if node is None or _depth >= _MAX_FOLD_DEPTH:
        return False  # None, or a pathologically deep +-chain -> non-foldable (never crash)
    if node.type in ("string", "template_string"):
        value = _string_value(node)
        if value is None or (node.type == "template_string" and "${" in value):
            return False  # a dynamic ${...} template -> non-foldable; never fold the param as a literal
        parts.append(_Part("lit", literal=value))
        return True
    if node.type == "identifier":
        if _text_if_short(node) != param:
            return False  # some other variable -> not statically foldable
        parts.append(_Part("param"))
        return True
    if node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is None or _text(operator) != "+":
            return False
        return _fold_into(
            node.child_by_field_name("left"), param, parts, keys, _depth + 1
        ) and _fold_into(node.child_by_field_name("right"), param, parts, keys, _depth + 1)
    if node.type == "subscript_expression":
        obj = node.child_by_field_name("object")
        index = node.child_by_field_name("index")
        if (
            obj is not None
            and obj.type == "object"
            and index is not None
            and index.type == "identifier"
            and _text_if_short(index) == param
        ):
            mapping = _string_object(obj)
            parts.append(_Part("hashmap", hashmap=mapping))
            keys.extend(mapping.keys())
            return True
        return False
    if node.type == "parenthesized_expression":
        inner = node.named_children
        return len(inner) == 1 and _fold_into(inner[0], param, parts, keys, _depth + 1)
    return False


def _string_object(obj: Node) -> dict[str, str]:
    """A ``{id: "hash"}`` map's string-literal entries. Non-literal values are dropped
    (their ids simply won't fold), so a partially-dynamic map still yields its static
    entries and never a guessed one."""
    result: dict[str, str] = {}
    for key, value_node in _object_pairs(obj).items():
        value = _string_value(value_node)
        if value is not None:
            result[key] = value
    return result


def _fold(parts: tuple[_Part, ...], chunk_id: str) -> str | None:
    out: list[str] = []
    for part in parts:
        if part.kind == "lit":
            out.append(part.literal)
        elif part.kind == "param":
            out.append(chunk_id)
        else:  # hashmap
            value = part.hashmap.get(chunk_id)
            if value is None:
                return None  # id not in the static map -> skip, never invent a hash
            out.append(value)
    return "".join(out)


def _collect_chunk_ids(root: Node, builder: _Builder) -> list[str]:
    """Chunk ids to enumerate: the ``<alias>.e(<id>)`` ensure-call sites (source order)
    then any map-only keys, de-duplicated. Scoping ``.e`` to the builder's own alias
    keeps an unrelated ``x.e(...)`` from injecting a bogus id."""
    ids: list[str] = []
    seen: set[str] = set()
    for node in _walk(root):
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            continue
        if _text_if_short(fn.child_by_field_name("property")) != "e":
            continue
        if _text_if_short(fn.child_by_field_name("object")) != builder.obj:
            continue
        call_args = _args(node)
        if len(call_args) != 1:
            continue
        chunk_id = _literal_id(call_args[0])
        if chunk_id is not None and chunk_id not in seen:
            seen.add(chunk_id)
            ids.append(chunk_id)
    for key in builder.hashmap_keys:
        if key not in seen:
            seen.add(key)
            ids.append(key)
    return ids


def _literal_id(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "number":
        return _text_if_short(node)
    if node.type in ("string", "template_string"):
        return _string_value(node)
    return None


def _find_public_path(root: Node, require_alias: str) -> str:
    """The literal ``<alias>.p = "…"`` public path, or ``""`` when it is assigned a
    runtime value (``.p = e``) — leaving the URL relative for the caller to resolve
    against the bundle's own URL."""
    for node in _walk(root):
        if node.type != "assignment_expression":
            continue
        left = node.child_by_field_name("left")
        if left is None or left.type != "member_expression":
            continue
        if _text_if_short(left.child_by_field_name("property")) != "p":
            continue
        if _text_if_short(left.child_by_field_name("object")) != require_alias:
            continue
        value = _string_value(node.child_by_field_name("right"))
        if value is not None:
            return value
    return ""


# --- conditional (ternary / if-chain) `.u` builders -------------------------- #
#
# Real Next.js/webpack rarely emit the subscript-map builder above; they emit the chunk
# URL as a ternary chain (`id===e?"url":…`) or a block-body if-chain
# (`(e)=>{if(id===e)return"url";…}`). js-recon reaches these by EXECUTING the arrow in a
# sandbox per scraped integer — we fold them statically instead (more precise: only ids
# with an explicit `id===e` test fold; the trailing default/`||e` catch-all arm is NOT
# tied to a specific id and is dropped, upholding the "never a guessed URL" contract).


def _enumerate_conditional(root: Node, *, max_urls: int, max_url_len: int) -> list[str]:
    """Enumerate a conditional-form `.u` builder (ternary chain or block-body if-chain)."""
    found = _find_u_fn_raw(root)
    if found is None:
        return []
    param, body, obj = found
    if body.end_byte - body.start_byte > _MAX_COND_BODY_SPAN:
        return []  # oversized builder -> not statically foldable (DoS bound)
    if body.type == "ternary_expression":
        id_urls = _fold_conditional_chain(body, param)
    elif body.type == "statement_block":
        id_urls = _fold_if_chain(body, param)
        if not id_urls:  # a block-body arrow that `return`s a ternary
            returned = _return_expr(body)
            if returned is not None and returned.type == "ternary_expression":
                id_urls = _fold_conditional_chain(returned, param)
    else:
        return []
    if not id_urls:
        return []

    prefix = _find_public_path(root, obj)
    urls: list[str] = []
    seen: set[str] = set()
    for _chunk_id, raw in id_urls:
        if len(urls) >= max_urls:
            break
        url = prefix + raw if prefix else raw
        if len(url) > max_url_len or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _find_u_fn_raw(root: Node) -> tuple[str, Node, str] | None:
    """Locate ``<obj>.u = <fn>`` and return ``(param, fn_body, obj)`` WITHOUT collapsing a
    block body to its return expr (unlike `_as_builder_fn`) — the if-chain form needs the
    raw block. ``None`` if absent / not a single-parameter function."""
    for node in _walk(root):
        if node.type != "assignment_expression":
            continue
        left = node.child_by_field_name("left")
        if left is None or left.type != "member_expression":
            continue
        if _text_if_short(left.child_by_field_name("property")) != "u":
            continue
        obj = _text_if_short(left.child_by_field_name("object"))
        if not obj:
            continue
        fn = node.child_by_field_name("right")
        if fn is None or fn.type not in ("arrow_function", "function_expression", "function"):
            continue
        param = _single_param(fn)
        if param is None:
            continue
        body = fn.child_by_field_name("body")
        if body is not None:
            return param, body, obj
    return None


def _fold_conditional_chain(node: Node | None, param: str) -> list[tuple[str, str]]:
    """Fold a ternary chain ``id===e ? <url> : <next>`` into ordered ``(id, url)`` pairs,
    walking the ``alternative`` iteratively (no recursion regardless of chain length). The
    final non-ternary ``alternative`` is the default arm and is dropped."""
    id_urls: list[tuple[str, str]] = []
    arms = 0
    while node is not None and node.type == "ternary_expression" and arms < _MAX_COND_ARMS:
        arms += 1
        chunk_id = _eq_test_id(node.child_by_field_name("condition"), param)
        consequence = node.child_by_field_name("consequence")
        if chunk_id is not None and consequence is not None:
            url = _fold_url_expr(consequence, param, chunk_id)
            if url is not None:
                id_urls.append((chunk_id, url))
        node = node.child_by_field_name("alternative")
    return id_urls


def _fold_if_chain(block: Node, param: str) -> list[tuple[str, str]]:
    """Fold a block-body if-chain ``{ if(id===e) return "url"; … }`` into ``(id, url)``
    pairs. A bare trailing ``return e+".js"`` (no ``id===e`` test) is the default and skipped."""
    id_urls: list[tuple[str, str]] = []
    arms = 0
    for stmt in block.named_children:
        if arms >= _MAX_COND_ARMS:
            break
        if stmt.type != "if_statement":
            continue
        arms += 1
        chunk_id = _eq_test_id(stmt.child_by_field_name("condition"), param)
        if chunk_id is None:
            continue
        returned = _return_arg(stmt.child_by_field_name("consequence"))
        if returned is None:
            continue
        url = _fold_url_expr(returned, param, chunk_id)
        if url is not None:
            id_urls.append((chunk_id, url))
    return id_urls


def _eq_test_id(condition: Node | None, param: str) -> str | None:
    """The chunk-id literal from a ``<param> === <literal>`` / ``<literal> === <param>``
    strict-equality test (unwrapping a parenthesized `if` condition), else ``None``."""
    while condition is not None and condition.type == "parenthesized_expression":
        inner = condition.named_children
        condition = inner[0] if len(inner) == 1 else None
    if condition is None or condition.type != "binary_expression":
        return None
    operator = condition.child_by_field_name("operator")
    if operator is None or _text(operator) not in ("===", "=="):
        return None
    left = condition.child_by_field_name("left")
    right = condition.child_by_field_name("right")
    if left is not None and left.type == "identifier" and _text_if_short(left) == param:
        return _literal_id(right)
    if right is not None and right.type == "identifier" and _text_if_short(right) == param:
        return _literal_id(left)
    return None


def _return_arg(node: Node | None) -> Node | None:
    """The returned expression of an `if` consequence — a ``return_statement`` directly, or
    the first ``return`` inside a ``{ … }`` consequence block."""
    if node is None:
        return None
    if node.type == "return_statement":
        returned = node.named_children
        return returned[0] if returned else None
    if node.type == "statement_block":
        return _return_expr(node)
    return None


def _fold_url_expr(node: Node, param: str, chunk_id: str) -> str | None:
    """Fold one conditional arm's URL expression (a ``+``-concat of literals / the param /
    a ``{id:hash}[param]`` map) to a concrete URL for ``chunk_id``, or ``None``."""
    folded = _fold_body(node, param)
    if folded is None:
        return None
    parts, _keys = folded
    return _fold(tuple(parts), chunk_id)
