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
from dataclasses import dataclass

from tree_sitter import Node

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
    """Map each ``export const/let/var NAME = "<string literal>"`` to its value.

    Only statically-certain string literals are returned (honesty). Re-exports
    (``export { X } from "./y"``) and non-literal export values are intentionally
    omitted from this increment — they would need graph traversal / data-flow and
    are absent rather than guessed.
    """
    exports: dict[str, str] = {}
    for node in _walk(root):
        if node.type != "export_statement":
            continue
        decl = node.child_by_field_name("declaration")
        # A re-export (`export { X } from "..."`) has no `declaration` field; skip.
        if decl is None or decl.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for child in decl.named_children:
            if child.type != "variable_declarator":
                continue
            name = child.child_by_field_name("name")
            value = child.child_by_field_name("value")
            if name is None or name.type != "identifier" or value is None:
                continue
            # Bail before an O(span) decode of a pathological giant literal (DoS).
            if value.end_byte - value.start_byte > _MAX_URL_SPAN:
                continue
            lit = _string_value(value)
            if lit is not None:
                exports[_text(name)] = lit
    return exports


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
