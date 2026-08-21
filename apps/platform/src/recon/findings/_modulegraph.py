"""Static ESM module graph for cross-module / cross-chunk const resolution (P1).

Pure, no execution. Given a recovered module's AST it reports (a) the string
constants that module EXPORTS and (b) the named bindings it IMPORTS from sibling
modules, plus a relative-specifier resolver. A later pass (analyze.py) builds a
run-level index of every asset's exports, then per consuming module resolves an
`import`ed name back to its exporter's literal so the sink resolver can fold a
cross-chunk `fetch(API_BASE + ORDERS_PATH)` whose operands live in another chunk.

Honesty over guessing (REQ-C2), same rule as the rest of the extractor: only a
statically-certain ``export const NAME = "<string literal>"`` crosses the module
boundary. A non-literal export is simply absent from the index — never guessed —
so a value we can't be sure of leaves the call ``unattributed`` exactly as today.

Scope (Increment 1): ORIGINAL / source-map-recovered ESM source — the path the
pipeline analyzes when a chunk ships a ``sourcesContent`` map. Minified bundler
output (webpack ``__webpack_require__`` numeric module maps, renamed/hashed ESM
re-exports) is a documented follow-on; the no-map build variants in
``test-targets/recon-range`` are its standing RED baseline.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

from tree_sitter import Node

from recon.findings._base_env import _declared_names
from recon.findings._jsast import _MAX_URL_SPAN, _PARSER, _string_value, _text, _walk

# A scheme://authority prefix (e.g. `webpack://recon-range`) that a recovered
# source path might retain, split off so relative resolution operates on the path
# portion and re-attaches the prefix. Sourcemapper normally strips the scheme when
# it rebuilds the tree (paths arrive here as plain relatives), so this is
# defense-in-depth for a map that leaks one through.
_SCHEME_PREFIX_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*://[^/]*)(/.*)?$")

# Extension/index candidates tried when matching a resolved specifier against the
# export index. recon-range imports carry explicit `.js`, but real ESM omits it.
_RESOLVE_SUFFIXES = ("", ".js", ".mjs", ".jsx", ".ts", ".tsx", "/index.js", "/index.mjs")


@dataclass(frozen=True)
class ImportBinding:
    """One `import { imported as local } from "specifier"` binding."""

    local: str  # name the importing module refers to
    imported: str  # name the source module exports
    specifier: str  # the module specifier, e.g. "./base.js"


def collect_module_exports(root: Node) -> dict[str, str]:
    """Map each exported name to its statically-certain string-literal value.

    Two shapes cross the boundary (honesty: only string literals, never guessed):
    - ``export const/let/var NAME = "<literal>"`` — the direct form (original source).
    - ``const LOCAL = "<literal>"; export { LOCAL as NAME }`` — the re-alias form a
      minifier emits (rollup/esbuild rename the const and list exports separately).
    A ``export { X } from "./y"`` re-export (has a ``source``) needs graph traversal
    and is skipped; a non-literal value is absent, not guessed.
    """
    exports: dict[str, str] = {}
    for node in _walk(root):
        if node.type != "export_statement":
            continue
        decl = node.child_by_field_name("declaration")
        if decl is not None and decl.type in ("lexical_declaration", "variable_declaration"):
            for child in decl.named_children:
                _add_string_export(child, exports)
    # Re-alias form: resolve `export { local as Name }` against the module's local
    # string consts. Deferred to a second pass so it only runs when such a clause
    # exists (the common original-source module has none).
    aliases = _reexport_aliases(root)
    if aliases:
        local_consts = _local_string_consts(root)
        for local, exported in aliases:
            if local in local_consts:
                exports[exported] = local_consts[local]
    return exports


def _add_string_export(declarator: Node, exports: dict[str, str]) -> None:
    """Record ``NAME = "<literal>"`` from an ``export const`` declarator."""
    if declarator.type != "variable_declarator":
        return
    name = declarator.child_by_field_name("name")
    value = declarator.child_by_field_name("value")
    if name is None or name.type != "identifier" or value is None:
        return
    if value.end_byte - value.start_byte > _MAX_URL_SPAN:
        return  # bail before an O(span) decode of a pathological giant literal (DoS)
    lit = _string_value(value)
    if lit is not None:
        exports[_text(name)] = lit


def _reexport_aliases(root: Node) -> list[tuple[str, str]]:
    """``export { local as Name }`` bindings (local, exported), skipping re-exports
    that pull ``from "./other"`` (those need graph traversal — a later increment)."""
    out: list[tuple[str, str]] = []
    for node in _walk(root):
        if node.type != "export_statement" or node.child_by_field_name("source") is not None:
            continue
        for spec in _walk(node):
            if spec.type != "export_specifier":
                continue
            local = spec.child_by_field_name("name")
            if local is None:
                continue
            alias = spec.child_by_field_name("alias")
            exported = _text(alias) if alias is not None else _text(local)
            out.append((_text(local), exported))
    return out


def _local_string_consts(root: Node) -> dict[str, str]:
    """Module-scope ``const/let/var NAME = "<literal>"`` values, POISON-SAFE: a name
    bound or reassigned more than once (shadowed, redeclared) is excluded rather
    than resolved to a possibly-wrong value — same discipline as
    :func:`recon.findings._base_env.collect_base_env` (REQ-C2 honesty). Only used to
    back an ``export { local as Name }`` re-alias, so a wrong value can never be
    presented as a cross-chunk URL."""
    poisoned = _declared_names(root)
    consts: dict[str, str] = {}
    for node in _walk(root):
        if node.type != "variable_declarator":
            continue
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is None or name.type != "identifier" or value is None:
            continue
        text = _text(name)
        if text in poisoned or value.end_byte - value.start_byte > _MAX_URL_SPAN:
            continue
        lit = _string_value(value)
        if lit is not None:
            consts[text] = lit
    return consts


def collect_named_imports(root: Node) -> list[ImportBinding]:
    """All ``import { A, B as C } from "spec"`` bindings.

    Named imports only: a default (``import Foo``) or namespace (``import * as
    NS``) import binds an object/callable, not a resolvable string const, so it
    contributes nothing to cross-module URL folding.
    """
    out: list[ImportBinding] = []
    for node in _walk(root):
        if node.type != "import_statement":
            continue
        source = node.child_by_field_name("source")
        specifier = _string_value(source) if source is not None else None
        if specifier is None:
            continue
        # `import_specifier` only ever appears inside `named_imports`, so walking
        # this statement's subtree for it captures exactly the named bindings.
        for spec_node in _walk(node):
            if spec_node.type != "import_specifier":
                continue
            name = spec_node.child_by_field_name("name")
            if name is None:
                continue
            alias = spec_node.child_by_field_name("alias")  # `imported as local`
            imported = _text(name)
            local = _text(alias) if alias is not None else imported
            out.append(ImportBinding(local=local, imported=imported, specifier=specifier))
    return out


def resolve_relative_specifier(importer_path: str, specifier: str) -> str | None:
    """Resolve a RELATIVE specifier against the importer's recovered module path.

    Returns ``None`` for a bare/package specifier (``"axios"``) — those aren't
    local modules and never carry a resolvable const. Pure posix path arithmetic;
    any ``scheme://authority`` prefix on the importer path is preserved. The
    output is compared against export-index keys (same ``f.path`` derivation), so
    resolution depends only on relative structure, which sourcemapper preserves
    consistently within one build.
    """
    if not specifier.startswith("."):
        return None
    prefix, path = _split_scheme_prefix(importer_path)
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
    return prefix + joined


def url_module_key(url: str) -> str:
    """Module-identity key for a NO-MAP chunk: the URL's path portion.

    Used when a chunk ships no source map, so its module identity is the served
    URL rather than a recovered ``f.path``. The leading ``/`` is deliberately kept
    so a URL key (``/assets/index-abc.js``) can never collide with a mapped
    module's relative ``f.path`` (``src/api/base.js``) in the shared export index.
    The SAME derivation keys the index and the importer side, and a relative
    specifier resolves path-only (no scheme to re-attach), so the two match — the
    hash in ``index-BLBrOdfO.js`` is preserved verbatim (never hash-collapsed like
    a normalized finding path would be, which would break the match).
    """
    return urlsplit(url).path or url


def build_cross_module_consts(
    importer_path: str,
    imports: list[ImportBinding],
    export_index: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Resolve a module's named imports to ``{local_name: exported string value}``.

    Import-filtered by construction: only names THIS module actually imports are
    considered, and each is looked up in the specifically-referenced source module
    — so a same-named local const in some unrelated module can never leak in and
    turn an honest ``unattributed`` call into a wrong guess (adversary finding 2).
    """
    resolved: dict[str, str] = {}
    for binding in imports:
        target = resolve_relative_specifier(importer_path, binding.specifier)
        if target is None:
            continue
        for candidate in (target + suffix for suffix in _RESOLVE_SUFFIXES):
            module = export_index.get(candidate)
            if module is not None and binding.imported in module:
                resolved[binding.local] = module[binding.imported]
                break
    return resolved


def _split_scheme_prefix(path: str) -> tuple[str, str]:
    match = _SCHEME_PREFIX_RE.match(path)
    if match:
        return match.group(1), match.group(2) or ""
    return "", path


def parse(source: str | bytes) -> Node:
    """Parse a unit's source to a tree-sitter root node (shared parser)."""
    data = source.encode("utf-8") if isinstance(source, str) else source
    return _PARSER.parse(data).root_node


# --- minified webpack module graph (Increment 2b) ---------------------------- #
#
# A no-map webpack chunk is a registry of numeric-keyed modules
# ``{389(module,exports,require){ ... require.d(exports, DEFN) ... }, ...}``; a
# consumer does ``var r = require(389); fetch(r.t + r.M)``. We resolve a cross-chunk
# member sink by: (1) indexing every module's statically-known string exports by
# module id, SCOPED per webpack build (`webpack_build_id`) because ids are unique
# only within one build; (2) mapping a consumer's ``var X = require(id)`` aliases to
# that build's modules; (3) folding ``X.prop`` at the sink. Honesty is preserved
# throughout: only string-resolvable exports are kept, ambiguous aliases are dropped.

# The jsonp global `webpackChunk<uniqueName>` webpack itself uses to keep separate
# builds apart — our per-build index key, so a third-party webpack widget's module
# `389` can never cross-wire into the first-party bundle's `389` (adversary F4).
_WEBPACK_CHUNK_RE = re.compile(r"webpackChunk([A-Za-z0-9_$]+)")

# Bound getter-arrow recursion (`() => () => …`) — a crafted deep chain would else
# overflow the stack (DoS); real export getters are `() => localConst`, depth 1.
_MAX_GETTER_DEPTH = 12


def webpack_build_id(source: str) -> str | None:
    """The webpack build's uniqueName (from its `webpackChunk<name>` jsonp global),
    or ``None`` if the source isn't a webpack chunk. Used to scope the module index
    per build. A false match only mis-scopes (→ no resolution), never mis-resolves."""
    match = _WEBPACK_CHUNK_RE.search(source)
    return match.group(1) if match else None


def _resolve_getter(node: Node | None, consts: dict[str, str], depth: int = 0) -> str | None:
    """Resolve a webpack export getter slot to a string: a local-const identifier, an
    arrow ``() => X`` (recurse into its body), a parenthesized wrapper, or a literal.
    Anything else (a function body, a computed expression) -> ``None`` (honest)."""
    if node is None or depth > _MAX_GETTER_DEPTH:
        return None
    if node.end_byte - node.start_byte > _MAX_URL_SPAN:
        return None  # bail before an O(span) decode of a pathological giant node (DoS)
    if node.type == "identifier":
        return consts.get(_text(node))
    if node.type == "arrow_function":
        return _resolve_getter(node.child_by_field_name("body"), consts, depth + 1)
    if node.type == "parenthesized_expression":
        inner = node.named_children
        return _resolve_getter(inner[0], consts, depth + 1) if len(inner) == 1 else None
    return _string_value(node)


def _decode_nd(defn: Node, consts: dict[str, str]) -> dict[str, str]:
    """Decode ``require.d(exports, DEFN)``'s DEFN to ``{export_name: string value}``.

    DEFN is either an OBJECT ``{name: () => getter}`` or the webpack-5 production
    ARRAY form, whose stride is VARIABLE (adversary F3): reading name then the next
    slot, a numeric ``0`` flag means a 3-slot value entry (``["M", 0, r]``) while any
    other slot IS the 2-slot getter (``["name", () => v]``) — mirroring webpack's own
    `d` runtime. Only string-resolvable exports are kept."""
    out: dict[str, str] = {}
    if defn.type == "array":
        elems = defn.named_children
        i = 0
        while i < len(elems):
            name = _string_value(elems[i])
            if name is None or i + 1 >= len(elems):
                break  # malformed / truncated -> stop rather than mis-pair
            flag = elems[i + 1]
            if flag.type == "number" and _text(flag) == "0":  # value entry, stride 3
                if i + 2 >= len(elems):
                    break
                value = _resolve_getter(elems[i + 2], consts)
                i += 3
            else:  # the slot itself is the getter, stride 2
                value = _resolve_getter(flag, consts)
                i += 2
            if value is not None:
                out[name] = value
    elif defn.type == "object":
        for pair in defn.named_children:
            if pair.type != "pair":
                continue  # skip spread / method shorthand
            key = pair.child_by_field_name("key")
            value_node = pair.child_by_field_name("value")
            if key is None or value_node is None:
                continue
            if key.type == "string":
                name = _string_value(key)
            elif key.type in ("property_identifier", "identifier"):
                name = _text(key)
            else:
                continue  # computed key -> not statically known
            value = _resolve_getter(value_node, consts)
            if name is not None and value is not None:
                out[name] = value
    return out


def _webpack_require_param(method: Node) -> str | None:
    """The 3rd formal parameter of a webpack module fn — its ``__webpack_require__``."""
    params = method.child_by_field_name("parameters")
    if params is None or len(params.named_children) < 3:
        return None
    require = params.named_children[2]
    return _text(require) if require.type == "identifier" else None


def _webpack_module_defs(root: Node) -> Iterator[tuple[str, str, Node]]:
    """Yield ``(module_id, require_name, body)`` for each numeric-keyed webpack module in
    the chunk that binds a require param. Covers the three real registry forms: a
    ``method_definition`` (``{389(e,t,n){…}}``) and a ``pair`` whose value is an
    ``arrow_function`` (``{389:(e,t,n)=>{…}}``) or a ``function_expression``
    (``{389:function(e,t,n){…}}``). Webpack 5 PRODUCTION emits the latter two (the
    method-shorthand alone was a fixture artifact); the ``require.d`` export gate in
    `collect_webpack_modules` + the poison-safe alias rules keep a non-module numeric-keyed
    function inert, so widening the accepted declaration form adds no false positives."""
    for node in _walk(root):
        if node.type == "method_definition":
            name = node.child_by_field_name("name")
            fn = node
        elif node.type == "pair":
            value = node.child_by_field_name("value")
            if value is None or value.type not in ("arrow_function", "function_expression"):
                continue
            name = node.child_by_field_name("key")
            fn = value
        else:
            continue
        body = fn.child_by_field_name("body")
        require = _webpack_require_param(fn)
        if name is not None and name.type == "number" and body is not None and require is not None:
            yield _text(name), require, body


def collect_webpack_modules(root: Node) -> dict[str, dict[str, str]]:
    """Index a chunk's webpack modules -> ``{module_id: {export_name: string value}}``.

    Scope: the numeric-keyed registry forms `_webpack_module_defs` accepts — a
    ``method_definition`` (``{389(e,t,n){…}}``) plus a ``pair`` whose value is an
    ``arrow_function`` (``{389:(e,t,n)=>{…}}``) or ``function_expression``
    (``{389:function(e,t,n){…}}``), the forms webpack 5 production emits. A sparse-array
    registry stays out of scope (lossy, never a wrong value). A stray numeric-keyed function
    with no ``require.d`` export call contributes nothing, so the ``.d``-gate keeps false
    positives inert."""
    modules: dict[str, dict[str, str]] = {}
    for module_id, require_name, body in _webpack_module_defs(root):
        consts = _local_string_consts(body)
        exports: dict[str, str] = {}
        for call in _walk(body):
            if call.type != "call_expression":
                continue
            fn = call.child_by_field_name("function")
            if fn is None or fn.type != "member_expression":
                continue
            obj = fn.child_by_field_name("object")
            prop = fn.child_by_field_name("property")
            if obj is None or prop is None or _text(prop) != "d" or _text(obj) != require_name:
                continue  # require.d(exports, DEFN) only
            args = call.child_by_field_name("arguments")
            if args is not None and len(args.named_children) >= 2:
                exports.update(_decode_nd(args.named_children[1], consts))
        if exports:
            modules[module_id] = exports
    return modules


def collect_webpack_requires(root: Node) -> dict[str, str]:
    """Map a chunk's ``var X = require(id)`` aliases -> ``{alias: module_id}``.

    Poison-safe (adversary F1): an alias whose name is bound/reassigned/shadowed more
    than once anywhere in the chunk (via `_declared_names`) OR that binds to more than
    one distinct module id is EXCLUDED — a flat chunk-level map must never mis-resolve
    a param-shadowed or reassigned name to a require it isn't (lossy but honest)."""
    poisoned = _declared_names(root)
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for _module_id, require_name, body in _webpack_module_defs(root):
        for decl in _walk(body):
            if decl.type != "variable_declarator":
                continue
            lhs = decl.child_by_field_name("name")
            rhs = decl.child_by_field_name("value")
            if (
                lhs is None
                or lhs.type != "identifier"
                or rhs is None
                or rhs.type != "call_expression"
            ):
                continue
            fn = rhs.child_by_field_name("function")
            if fn is None or fn.type != "identifier" or _text(fn) != require_name:
                continue  # X = require(...)
            args = rhs.child_by_field_name("arguments")
            arg = args.named_children[0] if args is not None and args.named_children else None
            if arg is None or arg.type != "number":
                continue
            alias, module_id = _text(lhs), _text(arg)
            if alias in aliases and aliases[alias] != module_id:
                ambiguous.add(alias)
            aliases[alias] = module_id
    return {a: m for a, m in aliases.items() if a not in poisoned and a not in ambiguous}
