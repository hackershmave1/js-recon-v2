"""Unit tests for static ESM import-specifier enumeration (esmimports, no execution).

Sibling to chunkenum_test.py: chunkenum folds webpack's COMPUTED `.u` chunk URLs; this
reads native-ESM LITERAL import specifiers (`import "./app.js"`) that Vite/Rollup/Rolldown
emit. Static `import`/`export ... from` are top-level-only per the ES spec, so the
enumerator scans the program's direct children — cheap, and complete for the static forms.
Dynamic `import()` is deliberately out of scope here (deferred to katana `-jc`).
"""

from __future__ import annotations

from recon.findings.esmimports import enumerate_import_urls


def test_side_effect_named_and_export_from() -> None:
    src = 'import"./a.js";import{x}from"./b.js";export{y}from"./c.js";export*from"./d.js";'
    assert enumerate_import_urls(src) == ["./a.js", "./b.js", "./c.js", "./d.js"]


def test_real_rolldown_entry_shape() -> None:
    # The VERIFIED hackerone main_js shape (Rolldown): three static imports the tool missed.
    src = (
        'import{o as e}from"./rolldown-runtime-DAXXjFlN.js";'
        'import{Vw as t,Xw as n}from"./vendor-C87ojrzg.js";'
        'import"./app-Cp_78317.js";n(),r();'
    )
    assert enumerate_import_urls(src) == [
        "./rolldown-runtime-DAXXjFlN.js",
        "./vendor-C87ojrzg.js",
        "./app-Cp_78317.js",
    ]


def test_bare_specifiers_and_non_js_assets_are_skipped() -> None:
    # A bare npm specifier is import-map-resolved, not a fetchable URL; .css/.json import
    # side-effects yield no endpoints.
    src = 'import"react";import"lodash-es";import"./styles.css";import cfg from"./data.json";'
    assert enumerate_import_urls(src) == []


def test_absolute_and_protocol_relative_kept_for_the_guard_to_scope() -> None:
    # The egress guard (not this filter) is the SSRF boundary — keep URL-shaped .js specifiers;
    # the caller resolves + validates each, dropping out-of-scope ones.
    src = 'import"https://acme.io/assets/x.js";import"//cdn.acme.io/y.mjs";'
    assert enumerate_import_urls(src) == ["https://acme.io/assets/x.js", "//cdn.acme.io/y.mjs"]


def test_query_and_hash_stripped_before_extension_test() -> None:
    # MUST-FIX: a Vite cache-busting `?v=hash` (or `#frag`) must not hide the .js extension.
    src = 'import"./app.js?v=abc123";import"./b.mjs#frag";'
    assert enumerate_import_urls(src) == ["./app.js?v=abc123", "./b.mjs#frag"]


def test_export_const_value_is_not_a_specifier() -> None:
    # Only `export ... FROM "x"` re-exports carry a module specifier; a plain
    # `export const a = "./b.js"` value must NOT be followed.
    src = 'export const a="./b.js";export default 1;import"./real.js";'
    assert enumerate_import_urls(src) == ["./real.js"]


def test_dynamic_import_is_out_of_scope_here() -> None:
    # Dynamic import() is deferred to katana -jc; the static top-level scan ignores it.
    src = 'const m=import("./lazy.js");import"./static.js";'
    assert enumerate_import_urls(src) == ["./static.js"]


def test_computed_import_is_never_guessed() -> None:
    src = "import(runtimeVar);import(x+y);"
    assert enumerate_import_urls(src) == []


def test_no_import_or_export_returns_empty() -> None:
    assert enumerate_import_urls('const x = fetch("/api/v3/orders");') == []


def test_duplicates_deduped_preserving_order() -> None:
    src = 'import"./a.js";import{x}from"./a.js";import"./b.js";'
    assert enumerate_import_urls(src) == ["./a.js", "./b.js"]


def test_max_urls_cap_bounds_output() -> None:
    src = "".join(f'import"./m{i}.js";' for i in range(10))
    assert len(enumerate_import_urls(src, max_urls=3)) == 3


def test_max_url_len_drops_overlong_specifier() -> None:
    overlong = "./" + "a" * 3000 + ".js"
    assert enumerate_import_urls(f'import"{overlong}";', max_url_len=2048) == []


def test_empty_and_data_and_blob_specifiers_dropped() -> None:
    # An empty specifier, a `data:` inline module, and a `blob:` URL are not fetchable JS chunks.
    src = 'import"";import"data:text/javascript,x";import u from"blob:abc";import"./real.js";'
    assert enumerate_import_urls(src) == ["./real.js"]


def test_import_attributes_do_not_break_source_lookup() -> None:
    # `import x from "./a.js" with { type: "json" }` — the source field is still ./a.js.
    src = 'import cfg from"./a.js"with{type:"json"};import"./b.js";'
    assert enumerate_import_urls(src) == ["./a.js", "./b.js"]
