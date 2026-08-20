"""Colocated pure-unit tests for the static ESM module graph (P1).

No infra — parse JS strings, assert exports / imports / specifier resolution and
the import-filtered cross-module const lookup. These pin the honesty invariants
the cross-chunk resolver rests on.
"""

from __future__ import annotations

from recon.findings._modulegraph import (
    ImportBinding,
    build_cross_module_consts,
    collect_module_exports,
    collect_named_imports,
    parse,
    resolve_relative_specifier,
)


def _exports(src: str) -> dict[str, str]:
    return collect_module_exports(parse(src))


def _imports(src: str) -> list[ImportBinding]:
    return collect_named_imports(parse(src))


# --- collect_module_exports --------------------------------------------------- #


def test_exports_string_const():
    assert _exports('export const API_BASE = "https://api.acme.com";') == {
        "API_BASE": "https://api.acme.com"
    }


def test_exports_multiple_declarators_and_kinds():
    src = 'export const A = "x", B = "y";\nexport let C = "z";\nexport var D = "w";'
    assert _exports(src) == {"A": "x", "B": "y", "C": "z", "D": "w"}


def test_non_exported_const_is_not_an_export():
    # A local const (even a string one) must NOT appear — only `export`ed names
    # cross the module boundary. This is what keeps bs-concat's local `resource`
    # out of the cross-module index.
    assert _exports('const secret = "/api/v1/widgets";') == {}


def test_non_literal_export_is_omitted_not_guessed():
    assert _exports("export const A = makeBase();") == {}
    assert _exports("export const A = B + C;") == {}


def test_reexport_is_skipped_this_increment():
    assert _exports('export { X } from "./other.js";') == {}


# --- collect_named_imports ---------------------------------------------------- #


def test_named_imports_with_and_without_alias():
    got = _imports('import { API_BASE, ORDERS_PATH as OP } from "./base.js";')
    assert got == [
        ImportBinding(local="API_BASE", imported="API_BASE", specifier="./base.js"),
        ImportBinding(local="OP", imported="ORDERS_PATH", specifier="./base.js"),
    ]


def test_default_and_namespace_imports_are_ignored():
    assert _imports('import Foo from "./m.js";') == []
    assert _imports('import * as NS from "./m.js";') == []
    # default + named: only the named binding is captured
    got = _imports('import Foo, { BAR } from "./m.js";')
    assert got == [ImportBinding(local="BAR", imported="BAR", specifier="./m.js")]


# --- resolve_relative_specifier ----------------------------------------------- #


def test_resolve_sibling_and_parent():
    assert resolve_relative_specifier("src/api/orders.js", "./base.js") == "src/api/base.js"
    assert resolve_relative_specifier("src/api/orders.js", "../lib/x.js") == "src/lib/x.js"
    assert resolve_relative_specifier("src/api/orders.js", "./sub/y.js") == "src/api/sub/y.js"


def test_resolve_bare_specifier_is_none():
    assert resolve_relative_specifier("src/api/orders.js", "axios") is None
    assert resolve_relative_specifier("src/api/orders.js", "@scope/pkg") is None


def test_resolve_preserves_scheme_authority_prefix():
    # webpack-style recovered path that leaked a scheme through: resolution acts
    # on the path portion, prefix re-attached.
    got = resolve_relative_specifier("webpack://recon-range/src/api/orders.js", "./base.js")
    assert got == "webpack://recon-range/src/api/base.js"


# --- build_cross_module_consts (the honesty-critical join) --------------------- #


_INDEX = {"src/api/base.js": {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}}


def test_cross_module_consts_resolves_imported_names():
    imports = _imports('import { API_BASE, ORDERS_PATH } from "./base.js";')
    assert build_cross_module_consts("src/api/orders.js", imports, _INDEX) == {
        "API_BASE": "https://api.acme.com",
        "ORDERS_PATH": "/api/v3/orders",
    }


def test_cross_module_consts_honors_alias():
    imports = _imports('import { ORDERS_PATH as OP } from "./base.js";')
    assert build_cross_module_consts("src/api/orders.js", imports, _INDEX) == {
        "OP": "/api/v3/orders"
    }


def test_cross_module_consts_extension_omitted():
    imports = _imports('import { API_BASE } from "./base";')  # no .js in specifier
    assert build_cross_module_consts("src/api/orders.js", imports, _INDEX) == {
        "API_BASE": "https://api.acme.com"
    }


def test_cross_module_consts_unknown_module_or_name_omitted():
    # imports a name the source module doesn't export -> absent, never guessed
    imports = _imports('import { NOPE } from "./base.js";')
    assert build_cross_module_consts("src/api/orders.js", imports, _INDEX) == {}
    # imports from a module not in the index -> absent
    imports = _imports('import { API_BASE } from "./missing.js";')
    assert build_cross_module_consts("src/api/orders.js", imports, _INDEX) == {}


def test_cross_module_consts_is_import_filtered_no_name_collision_leak():
    # A module that does NOT import API_BASE must get nothing, even though some
    # OTHER module in the index exports that name. This is the guard against a
    # global name lookup fabricating a value (adversary finding 2).
    assert build_cross_module_consts("src/api/orders.js", [], _INDEX) == {}
