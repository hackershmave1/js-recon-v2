"""Unit tests for native-ESM chunk-specifier enumeration (esmimports, no execution).

Sibling to chunkenum_test.py: chunkenum folds webpack's COMPUTED `.u` chunk URLs; this reads
native-ESM LITERAL specifiers that Vite/Rollup/Rolldown emit — both STATIC declarations
(`import "./app.js"`) and DYNAMIC lazy-route imports (`import("./route.js")`). Static
import/export are top-level per the ES spec; a dynamic `import()` nests anywhere, so the whole
tree is walked. Only statically-known specifiers cross — a `${...}` template or a computed
`import(x)` yields nothing.
"""

from __future__ import annotations

from recon.findings.esmimports import enumerate_esm_chunk_urls


def test_side_effect_named_and_export_from() -> None:
    src = 'import"./a.js";import{x}from"./b.js";export{y}from"./c.js";export*from"./d.js";'
    assert enumerate_esm_chunk_urls(src) == ["./a.js", "./b.js", "./c.js", "./d.js"]


def test_real_rolldown_entry_shape() -> None:
    # The VERIFIED hackerone main_js shape (Rolldown): three STATIC imports the tool missed.
    src = (
        'import{o as e}from"./rolldown-runtime-DAXXjFlN.js";'
        'import{Vw as t,Xw as n}from"./vendor-C87ojrzg.js";'
        'import"./app-Cp_78317.js";n(),r();'
    )
    assert enumerate_esm_chunk_urls(src) == [
        "./rolldown-runtime-DAXXjFlN.js",
        "./vendor-C87ojrzg.js",
        "./app-Cp_78317.js",
    ]


def test_real_rolldown_dynamic_route_shape() -> None:
    # The VERIFIED hackerone app-chunk shape: a lazy route via a template-literal dynamic import
    # nested inside a `__vitePreload`-style wrapper (`r(()=>A(()=>import(`./page.js`),dep))`).
    src = "r(()=>A(()=>import(`./bug_bounty_programs_page-B2Bil96C.js`),1))"
    assert enumerate_esm_chunk_urls(src) == ["./bug_bounty_programs_page-B2Bil96C.js"]


def test_dynamic_import_string_and_template_specifiers() -> None:
    # A dynamic import resolves from a plain string OR a no-substitution template literal.
    src = 'import("./route_a.js");import(`./route_b.js`);'
    assert enumerate_esm_chunk_urls(src) == ["./route_a.js", "./route_b.js"]


def test_dynamic_import_nested_anywhere_is_reached() -> None:
    # Dynamic import() is not top-level; it lives inside a callback here. The full-tree walk
    # (unlike the top-level static scan) must still reach it, ahead of the later static import.
    src = 'x(()=>import(`./lazy_page.js`));import"./static.js";'
    assert enumerate_esm_chunk_urls(src) == ["./lazy_page.js", "./static.js"]


def test_dynamic_interpolated_template_is_computed() -> None:
    # A `${...}` in the template makes the specifier runtime-computed -> honestly dropped; the
    # sibling static import still resolves.
    src = 'import(`./route-${id}.js`);import"./real.js";'
    assert enumerate_esm_chunk_urls(src) == ["./real.js"]


def test_dynamic_import_ignores_trailing_options_arg() -> None:
    # `import("./x.js", { with: { type: "..." } })` — the FIRST arg is the specifier; a trailing
    # options object must not suppress it.
    src = 'import("./x.js",{with:{type:"json"}});'
    assert enumerate_esm_chunk_urls(src) == ["./x.js"]


def test_static_and_dynamic_combined_in_source_order() -> None:
    # A real chunk mixes both forms; they come back de-duplicated in source order.
    src = 'import"./a.js";const p=()=>import(`./b.js`);export*from"./c.js";'
    assert enumerate_esm_chunk_urls(src) == ["./a.js", "./b.js", "./c.js"]


def test_bare_specifiers_and_non_js_assets_are_skipped() -> None:
    # A bare npm specifier is import-map-resolved, not a fetchable URL; .css/.json import
    # side-effects yield no endpoints. Holds for the dynamic form too.
    src = (
        'import"react";import"lodash-es";import"./styles.css";'
        'import cfg from"./data.json";import("./theme.css");'
    )
    assert enumerate_esm_chunk_urls(src) == []


def test_absolute_and_protocol_relative_kept_for_the_guard_to_scope() -> None:
    # The egress guard (not this filter) is the SSRF boundary — keep URL-shaped .js specifiers;
    # the caller resolves + validates each, dropping out-of-scope ones.
    src = 'import"https://acme.io/assets/x.js";import"//cdn.acme.io/y.mjs";'
    assert enumerate_esm_chunk_urls(src) == ["https://acme.io/assets/x.js", "//cdn.acme.io/y.mjs"]


def test_query_and_hash_stripped_before_extension_test() -> None:
    # MUST-FIX: a Vite cache-busting `?v=hash` (or `#frag`) must not hide the .js extension.
    src = 'import"./app.js?v=abc123";import"./b.mjs#frag";'
    assert enumerate_esm_chunk_urls(src) == ["./app.js?v=abc123", "./b.mjs#frag"]


def test_export_const_value_is_not_a_specifier() -> None:
    # Only `export ... FROM "x"` re-exports carry a module specifier; a plain
    # `export const a = "./b.js"` value must NOT be followed.
    src = 'export const a="./b.js";export default 1;import"./real.js";'
    assert enumerate_esm_chunk_urls(src) == ["./real.js"]


def test_computed_dynamic_import_is_never_guessed() -> None:
    # A variable or expression argument to import() is computed -> nothing is guessed.
    src = "import(runtimeVar);import(x+y);import(chunks[i]);"
    assert enumerate_esm_chunk_urls(src) == []


def test_no_import_or_export_returns_empty() -> None:
    assert enumerate_esm_chunk_urls('const x = fetch("/api/v3/orders");') == []


def test_duplicates_deduped_preserving_order() -> None:
    # De-dup spans both forms: a chunk both statically and dynamically imported appears once.
    src = 'import"./a.js";import{x}from"./a.js";import("./a.js");import"./b.js";'
    assert enumerate_esm_chunk_urls(src) == ["./a.js", "./b.js"]


def test_max_urls_cap_bounds_output() -> None:
    src = "".join(f'import"./m{i}.js";' for i in range(10))
    assert len(enumerate_esm_chunk_urls(src, max_urls=3)) == 3


def test_max_url_len_drops_overlong_specifier() -> None:
    overlong = "./" + "a" * 3000 + ".js"
    assert enumerate_esm_chunk_urls(f'import"{overlong}";', max_url_len=2048) == []


def test_empty_and_data_and_blob_specifiers_dropped() -> None:
    # An empty specifier, a `data:` inline module, and a `blob:` URL are not fetchable JS chunks.
    src = 'import"";import"data:text/javascript,x";import u from"blob:abc";import"./real.js";'
    assert enumerate_esm_chunk_urls(src) == ["./real.js"]


def test_import_attributes_do_not_break_source_lookup() -> None:
    # `import x from "./a.js" with { type: "json" }` — the source field is still ./a.js.
    src = 'import cfg from"./a.js"with{type:"json"};import"./b.js";'
    assert enumerate_esm_chunk_urls(src) == ["./a.js", "./b.js"]
