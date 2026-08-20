"""Unit tests for static webpack lazy-chunk URL enumeration (chunkenum, no execution)."""

from __future__ import annotations

from recon.findings.chunkenum import enumerate_chunk_urls


def test_recon_range_shape_ids_from_ensure_calls() -> None:
    # The real recon-range webpack-nomap shape: no inline hash map, ids come from
    # n.e(<id>) call sites, builder is `e => e + ".chunk.js"`.
    src = 'var n={};n.u=e=>e+".chunk.js";n.e(489);n.e(522);'
    assert enumerate_chunk_urls(src) == ["489.chunk.js", "522.chunk.js"]


def test_hashmap_shape_ids_from_map_keys() -> None:
    src = 'n.u=e=>"static/chunks/"+e+"."+{100:"a1b2",200:"c3d4"}[e]+".js";'
    assert enumerate_chunk_urls(src) == [
        "static/chunks/100.a1b2.js",
        "static/chunks/200.c3d4.js",
    ]


def test_literal_public_path_is_prefixed() -> None:
    src = 'n.p="/assets/";n.u=e=>e+".chunk.js";n.e(7);'
    assert enumerate_chunk_urls(src) == ["/assets/7.chunk.js"]


def test_dynamic_public_path_leaves_url_relative() -> None:
    # n.p=e (runtime document base) is not a literal -> no prefix, relative URL.
    src = 'n.p=e;n.u=e=>e+".chunk.js";n.e(7);'
    assert enumerate_chunk_urls(src) == ["7.chunk.js"]


def test_function_expression_and_block_body_form() -> None:
    src = 'n.u=function(e){return "c/"+e+".js"};n.e(3);'
    assert enumerate_chunk_urls(src) == ["c/3.js"]


def test_non_foldable_builder_enumerates_nothing() -> None:
    # A computed builder we cannot statically fold -> fail-safe empty, never guessed.
    src = "n.u=e=>someFn(e);n.e(1);"
    assert enumerate_chunk_urls(src) == []


def test_no_webpack_builder_returns_empty() -> None:
    assert enumerate_chunk_urls('const x = fetch("/api/v3/orders");') == []


def test_id_missing_from_hashmap_is_skipped_not_invented() -> None:
    # id 999 has no hash entry -> skipped (no invented hash); 100 present -> enumerated.
    src = 'n.u=e=>e+"."+{100:"a1b2"}[e]+".js";n.e(100);n.e(999);'
    assert enumerate_chunk_urls(src) == ["100.a1b2.js"]


def test_ensure_call_scoped_to_builder_alias() -> None:
    # x.e(42) belongs to a different object than the builder alias n -> ignored.
    src = 'n.u=e=>e+".chunk.js";n.e(1);x.e(42);'
    assert enumerate_chunk_urls(src) == ["1.chunk.js"]


def test_duplicate_ids_are_deduped() -> None:
    src = 'n.u=e=>e+".chunk.js";n.e(5);n.e(5);'
    assert enumerate_chunk_urls(src) == ["5.chunk.js"]


def test_max_urls_cap_bounds_output() -> None:
    calls = "".join(f"n.e({i});" for i in range(10))
    src = 'n.u=e=>e+".chunk.js";' + calls
    assert len(enumerate_chunk_urls(src, max_urls=3)) == 3


def test_over_long_url_is_dropped() -> None:
    long_segment = "x" * 5000
    src = f'n.u=e=>"{long_segment}"+e+".chunk.js";n.e(1);'
    assert enumerate_chunk_urls(src, max_url_len=2048) == []
