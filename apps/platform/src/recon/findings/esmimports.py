"""Native-ESM import-specifier enumeration (Vite/Rollup/Rolldown, NO execution).

Modern bundlers split an app into ES-module chunks wired by native imports — an entry's
STATIC ``import "./app-Cp.js"`` plus each module's DYNAMIC, lazily-loaded
``import("./route_page.js")`` (route/feature code splitting). Those chunks hold the app's real
code (endpoints, secrets), but the static crawl (katana HTML tags) and the WEBPACK-only
``chunkenum`` both miss them: there is no ``<script>`` tag and no ``__webpack_require__.u``
builder, only a bare ESM specifier.

This module recovers those specifiers *statically* — it never executes anything and never
guesses. It parses the module once and walks the tree for three shapes:
- ``import "x"`` / ``import ... from "x"`` — static top-level import declarations,
- ``export ... from "x"`` — re-exports (a plain ``export const a = "./b"`` value is NOT one),
- ``import("x")`` — a dynamic import whose specifier is a STATIC string OR a NO-substitution
  template literal (``import(`./route.js`)`` — the shape Rolldown/Vite emit for lazy routes). A
  ``${...}`` template or a variable (``import(runtimeVar)``) is COMPUTED — not statically
  knowable — and yields nothing.

Static import/export are top-level-only per the ES spec, but a dynamic ``import()`` nests
anywhere (inside an arrow / callback), so the tree is walked in FULL — bounded by the shared
``_MAX_AST_NODES`` DoS budget (a pathologically large tree stops at the cap, a best-effort
miss, never an unbounded walk).

Honesty (REQ-C2), same rule as ``chunkenum``: the enumerated specifiers are content-derived
and therefore UNTRUSTED — the caller MUST resolve each against the module's own URL and route
it through ``fetch.egress.validate_target`` (they can never widen scope), and cap the
count/length before seeding (the ceilings here bound both). The relative/``.js`` filter is a
noise+cost filter, NOT the security boundary — that is the egress guard.
"""

from __future__ import annotations

from tree_sitter import Node

from recon.findings._jsast import _MAX_AST_NODES, _PARSER, _string_value, _walk

_DEFAULT_MAX_URLS = 512
_DEFAULT_MAX_URL_LEN = 2048

# A followable specifier resolves to a fetchable JS chunk; other extensions (.css/.json/…) are
# side-effect/asset imports that yield no endpoints.
_JS_SUFFIXES = (".js", ".mjs", ".cjs")


def enumerate_esm_chunk_urls(
    source: str,
    *,
    max_urls: int = _DEFAULT_MAX_URLS,
    max_url_len: int = _DEFAULT_MAX_URL_LEN,
) -> list[str]:
    """Statically enumerate a module's native-ESM chunk specifiers from ``source``.

    Covers both STATIC ``import``/``export … from`` declarations and DYNAMIC ``import("…")``
    calls (string or no-substitution template literal). Returns a de-duplicated, order-preserving
    list of relative/URL specifiers whose path resolves to a ``.js``/``.mjs``/``.cjs`` chunk
    (bare npm specifiers and non-JS assets are dropped). Empty when ``source`` has no followable
    import — self-gating, safe to call on every asset. The caller resolves each against the
    module's URL and validates it through the egress guard (scope is never widened here).
    """
    root = _PARSER.parse(source.encode("utf-8")).root_node
    urls: list[str] = []
    seen: set[str] = set()
    # A dynamic import() can nest anywhere, so the whole tree is walked (bounded by the shared
    # DoS budget); static import/export are top-level but a full walk reaches them too, so one
    # pass in source order covers all three shapes. Pre-order == source order (see `_walk`).
    for node in _walk(root, limit=_MAX_AST_NODES):
        if node.type == "import_statement":
            spec = _import_source(node)
        elif node.type == "export_statement":
            spec = _export_source(node)  # only a `... from "x"` re-export carries a source
        elif node.type == "call_expression":
            spec = _dynamic_import_source(node)  # None unless it's import("<static string>")
        else:
            continue
        if spec is None or len(spec) > max_url_len or spec in seen or not _is_followable_js(spec):
            continue
        seen.add(spec)
        urls.append(spec)
        if len(urls) >= max_urls:
            break
    return urls


def _import_source(node: Node) -> str | None:
    """The module specifier of an ``import_statement`` — its single source string.

    Covers ``import "x"`` (side-effect), ``import d from "x"``, ``import {a} from "x"``, and
    ``import * as ns from "x"``. The only string in an import statement is the source (the
    bindings are identifiers), so a ``source``-field lookup with a string-child fallback is
    unambiguous."""
    src = node.child_by_field_name("source")
    if src is None:
        strings = [child for child in node.children if child.type == "string"]
        src = strings[-1] if strings else None
    return _string_value(src) if src is not None else None


def _export_source(node: Node) -> str | None:
    """The module specifier of an ``export ... from "x"`` re-export, or ``None``.

    Keyed on the ``source`` FIELD (present only for a re-export), NOT any string child — so a
    plain ``export const a = "./b.js"`` value string is never mistaken for a specifier."""
    src = node.child_by_field_name("source")
    if src is not None and src.type == "string":
        return _string_value(src)
    return None


def _dynamic_import_source(node: Node) -> str | None:
    """The specifier of a dynamic ``import("…")`` call, or ``None`` when not statically known.

    A dynamic import parses as a ``call_expression`` whose ``function`` field is an ``import``
    node (``import.meta`` is a member expression, not a call, so it is excluded). Only the FIRST
    argument (the specifier; a trailing options object is ignored) yields a URL, and only when it
    is a plain string or a NO-substitution template literal — a ``${…}`` template or a variable/
    expression is COMPUTED and honestly yields nothing (never a guessed chunk URL)."""
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "import":
        return None
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    named = args.named_children
    if not named:
        return None
    arg = named[0]
    if arg.type == "string":
        return _string_value(arg)
    if arg.type == "template_string":
        # A `${…}` substitution makes the specifier runtime-computed, not statically resolvable.
        if any(child.type == "template_substitution" for child in arg.children):
            return None
        return _string_value(arg)
    return None


def _is_followable_js(spec: str) -> bool:
    """Whether ``spec`` is a fetchable JS chunk URL rather than a bare npm specifier.

    A relative (``./x``, ``../x``, ``/x``, ``//host/x``) or absolute (``https://host/x``)
    specifier whose path — with any ``?query``/``#hash`` stripped (Vite cache-busting) — ends
    in ``.js``/``.mjs``/``.cjs``. A bare specifier (``react``, ``lodash-es``) is
    import-map-resolved, not a URL, so it is dropped. Scope filtering is the caller's egress
    guard, not this function."""
    if not (spec.startswith((".", "/")) or "://" in spec):
        return False
    path = spec.split("?", 1)[0].split("#", 1)[0]
    return path.endswith(_JS_SUFFIXES)
