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


def test_deep_concat_chain_fails_safe_without_crashing() -> None:
    # A pathologically deep +-chain must degrade to non-foldable, never RecursionError
    # (the module's "never raises" contract holds independent of any caller's try/except).
    body = "e" + '+"a"' * 200
    assert enumerate_chunk_urls(f"n.u=e=>{body};n.e(1);") == []


def test_template_literal_with_substitution_is_non_foldable() -> None:
    # `static/${e}.js` must NOT fold ${e} verbatim into a literal (a bogus URL); it's dynamic.
    assert enumerate_chunk_urls("n.u=e=>`static/${e}.js`;n.e(1);") == []


def test_plain_backtick_template_without_substitution_still_folds() -> None:
    # A backtick string with no ${...} is a plain literal and folds normally.
    assert enumerate_chunk_urls('n.u=e=>`c/`+e+".js";n.e(3);') == ["c/3.js"]


# --- conditional (ternary / if-chain) builders: the real Next.js/webpack shape --------- #


def test_ternary_chain_param_in_consequent_folds_and_drops_default() -> None:
    # Real Next.js/webpack shape (Asana): `.u` is a ternary chain whose consequent
    # concatenates the id; the trailing default arm is a catch-all NOT tied to a specific
    # id, so it is dropped (never a guessed URL).
    src = (
        'o.p="/_next/";'
        'o.u=e=>11973===e?"static/chunks/"+e+"-aaa.js"'
        ':86877===e?"static/chunks/"+e+"-bbb.js"'
        ':"static/chunks/"+e+".js";'
    )
    assert enumerate_chunk_urls(src) == [
        "/_next/static/chunks/11973-aaa.js",
        "/_next/static/chunks/86877-bbb.js",
    ]


def test_ternary_chain_baked_in_id_literal() -> None:
    # Figma shape: each consequent is a plain literal with the id already baked in.
    src = (
        'o.u=e=>2484===e?"static/chunks/2484-aaa.js":8146===e?"static/chunks/8146-bbb.js":e+".js";'
    )
    assert enumerate_chunk_urls(src) == [
        "static/chunks/2484-aaa.js",
        "static/chunks/8146-bbb.js",
    ]


def test_ternary_function_expression_body_via_return() -> None:
    # Stripe shape: function(e){return <ternary>}.
    src = 'o.u=function(e){return 23526===e?"c/"+e+"-a.js":48759===e?"c/"+e+"-b.js":"x"};'
    assert enumerate_chunk_urls(src) == ["c/23526-a.js", "c/48759-b.js"]


def test_ternary_reversed_equality_operands() -> None:
    # `e===id` reads the constant off the RIGHT of ===; must fold same as `id===e`.
    src = 'o.u=e=>e===7?"c/7.js":e===8?"c/8.js":"z";'
    assert enumerate_chunk_urls(src) == ["c/7.js", "c/8.js"]


def test_if_chain_block_body_form() -> None:
    # js-recon's if-chain shape: (e)=>{if(id===e)return"x.js";…}. The bare trailing
    # `return e+".js"` has no id test -> dropped.
    src = 'o.u=e=>{if(123===e)return"c/a.js";if(456===e)return"c/b.js";return e+".js"};'
    assert enumerate_chunk_urls(src) == ["c/a.js", "c/b.js"]


def test_if_chain_with_ensure_sites_does_not_leak_default_arm() -> None:
    # §4 gate-2 CONFIRMED honesty fix: a block-body if-chain with a param-concat DEFAULT arm AND
    # real `.e(id)` sites must fold the `id===` arms and DROP the default — never apply the
    # default's format to a real id (which invents a URL the runtime never requests). Pre-fix the
    # template path collapsed the block to its trailing `return "static/chunks/"+e+".js"`, mistook
    # it for the builder, and folded it against 374/929 -> `.../374.js` instead of the real
    # `.../374.abc123.js` (an `if`-arm URL the runtime actually returns for id 374).
    src = (
        'r.u=e=>{if(374===e)return"static/chunks/374.abc123.js";'
        'if(929===e)return"static/chunks/929.def456.js";'
        'return"static/chunks/"+e+".js"};'
        "r.e(374);r.e(929);"
    )
    assert enumerate_chunk_urls(src) == [
        "static/chunks/374.abc123.js",
        "static/chunks/929.def456.js",
    ]


def test_branching_wrapper_default_arm_never_leaks_invented_urls() -> None:
    # §4 gate-2 follow-up: the honesty fix keys on the single-return SHAPE, not on `if_statement`
    # specifically, so EVERY branching wrapper (nested block / switch / try / labeled) that leaves
    # a foldable trailing `return "<template>"` is refused as a template and routed to the
    # conditional parser — which folds only recognised direct-child `id===` arms and drops the
    # rest. None may apply the default's format to a real `.e(id)` id (that invents `g/374.js`).
    ensure = "r.e(374);r.e(929);"
    for builder in (
        'r.u=e=>{{if(374===e)return"real-374.js"}return"g/"+e+".js"};',  # nested block
        'r.u=e=>{switch(e){case 374:return"real-374.js"}return"g/"+e+".js"};',  # switch
        'r.u=e=>{try{if(374===e)return"real-374.js"}catch(x){}return"g/"+e+".js"};',  # try/catch
        'r.u=e=>{L:{if(374===e)return"real-374.js"}return"g/"+e+".js"};',  # labeled block
    ):
        out = enumerate_chunk_urls(builder + ensure)
        assert "g/374.js" not in out and "g/929.js" not in out, (builder, out)


def test_ternary_public_path_prefix_applied() -> None:
    src = 'n.p="/assets/";n.u=e=>7===e?"c/7-h.js":"d";'
    assert enumerate_chunk_urls(src) == ["/assets/c/7-h.js"]


def test_deep_ternary_chain_is_iterative_and_capped() -> None:
    # A long ternary chain must fold iteratively (no RecursionError) and honor max_urls.
    # 700 arms comfortably exceeds the small template span cap (8192 B) — this also guards
    # that a real, large ternary `.u` body (Asana's is ~13 KB) is not rejected.
    arms = "".join(f'{i}===e?"c/{i}.js":' for i in range(700)) + '"d"'
    assert len(f"o.u=e=>{arms};") > 8192
    out = enumerate_chunk_urls(f"o.u=e=>{arms};", max_urls=50)
    assert len(out) == 50
    assert out[0] == "c/0.js"


def test_if_chain_arm_cap_is_enforced_no_recursion() -> None:
    # A pathological block-body if-chain past `_MAX_COND_ARMS` must be arm-capped and iterative
    # (no RecursionError), independent of `max_urls`. Guards the arm cap the deep-ternary test
    # above (700 arms, under the cap) does not reach.
    from recon.findings.chunkenum import _MAX_COND_ARMS

    arms = "".join(f'if({i}===e)return"c/{i}.js";' for i in range(_MAX_COND_ARMS + 100))
    out = enumerate_chunk_urls(f"o.u=e=>{{{arms}return e}};", max_urls=1_000_000)
    assert len(out) == _MAX_COND_ARMS  # folded exactly the cap, no crash


def test_conditional_builder_with_no_id_tests_enumerates_nothing() -> None:
    # A conditional whose arms are not `id===e` tests -> nothing folds (fail-safe).
    assert enumerate_chunk_urls('o.u=e=>cond(e)?"a.js":"b.js";') == []
