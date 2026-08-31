"""Static native-ESM import-specifier enumeration (Vite/Rollup/Rolldown, NO execution).

Modern bundlers split an app into ES-module chunks wired by NATIVE static imports —
``import "./app-Cp_78317.js"`` inside an entry module — resolved by the browser's own module
loader. Those chunks hold the app's real code (endpoints, secrets), but the static crawl
(katana HTML tags + dynamic ``import()``) and the WEBPACK-only ``chunkenum`` both miss them:
there is no ``<script>`` tag and no ``__webpack_require__.u`` builder, only a bare ESM
specifier.

This module recovers those specifiers *statically*. Static ``import`` / ``export ... from``
declarations are TOP-LEVEL-ONLY per the ES spec, so it scans the program's direct children
(cheap — O(top-level statements), not a full-tree walk) and reads each declaration's literal
source string. It never executes anything and never guesses: only a string LITERAL crosses
into a specifier (a computed ``import(x)`` yields nothing), and an unparseable module yields
an empty list.

Scope: STATIC imports only. Dynamic ``import("...")`` is nestable anywhere (a full-tree walk)
and already overlaps katana's ``-jc`` dynamic-import crawl, so it is deliberately deferred.

Honesty (REQ-C2), same rule as ``chunkenum``: the enumerated specifiers are content-derived
and therefore UNTRUSTED — the caller MUST resolve each against the module's own URL and route
it through ``fetch.egress.validate_target`` (they can never widen scope), and cap the
count/length before seeding (the ceilings here bound both). The relative/``.js`` filter is a
noise+cost filter, NOT the security boundary — that is the egress guard.
"""

from __future__ import annotations

from tree_sitter import Node

from recon.findings._jsast import _PARSER, _string_value

_DEFAULT_MAX_URLS = 512
_DEFAULT_MAX_URL_LEN = 2048

# A followable specifier resolves to a fetchable JS chunk; other extensions (.css/.json/…) are
# side-effect/asset imports that yield no endpoints.
_JS_SUFFIXES = (".js", ".mjs", ".cjs")


def enumerate_import_urls(
    source: str,
    *,
    max_urls: int = _DEFAULT_MAX_URLS,
    max_url_len: int = _DEFAULT_MAX_URL_LEN,
) -> list[str]:
    """Statically enumerate a module's native-ESM import specifiers from ``source``.

    Returns a de-duplicated, order-preserving list of relative/URL specifiers whose path
    resolves to a ``.js``/``.mjs``/``.cjs`` chunk (bare npm specifiers and non-JS assets are
    dropped). Empty when ``source`` has no static import/export or none is followable —
    self-gating, safe to call on every asset. The caller resolves each against the module's URL
    and validates it through the egress guard (scope is never widened here).
    """
    root = _PARSER.parse(source.encode("utf-8")).root_node
    urls: list[str] = []
    seen: set[str] = set()
    # Static import/export declarations are top-level-only (ES spec), so the program's direct
    # children are the complete set — no full-tree walk needed.
    for node in root.named_children:
        if node.type == "import_statement":
            spec = _import_source(node)
        elif node.type == "export_statement":
            spec = _export_source(node)  # only a `... from "x"` re-export carries a source
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
