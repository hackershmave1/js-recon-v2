from recon.findings.techdetect import compile as tc


def test_compile_and_search_capture_group():
    pattern = tc.compile_pattern(r"nginx(?:/([\d.]+))?")
    match = pattern.search("nginx/1.25.3")
    assert match is not None
    assert match.groups() == ("1.25.3",)


def test_compile_is_case_insensitive_by_default():
    # HTTP header/token matching is case-insensitive (Wappalyzer default).
    assert tc.compile_pattern("express").search("X-Powered-By: Express") is not None


def test_try_compile_returns_none_for_a_lookbehind_pattern():
    # RE2 rejects lookbehind at compile time (T4) -> a soft skip, never a raise.
    assert tc.try_compile(r"(?<!elo\.io)/cargo\.") is None


def test_try_compile_returns_a_pattern_for_a_valid_source():
    compiled = tc.try_compile(r"jquery(?:-([\d.]+))?\.js")
    assert compiled is not None
    assert compiled.search("jquery-3.5.1.js") is not None


def test_compile_js_surface_keeps_distinctive_keys_and_drops_fp_magnets():
    raw = {"T": {"cats": [1], "js": {"__NEXT_DATA__": "", "$nuxt": "", "Vue": "", "core": ""}}}
    surface, skipped = tc.compile_js_surface(raw)  # type: ignore[arg-type]
    # Vue (<4 chars) and core (bare word <8 chars) are FP magnets -> never enter the Set.
    assert {p.key for p in surface.patterns} == {"__NEXT_DATA__", "$nuxt"}
    # Escaped literals never RE2-reject; the filter drops are by design, not rejects (T4).
    assert skipped == 0


def test_compile_js_surface_is_case_sensitive():
    raw = {"T": {"cats": [1], "js": {"__Marker__": ""}}}
    surface, _ = tc.compile_js_surface(raw)  # type: ignore[arg-type]
    assert surface.matcher.Match("x __Marker__ y")  # exact case hits
    assert surface.matcher.Match("x __marker__ y") is None  # wrong case misses


def test_compile_js_surface_pattern_index_aligns_with_set_match():
    raw = {
        "Alpha": {"cats": [1], "js": {"__alphaglobal__": ""}},
        "Beta": {"cats": [1], "js": {"__betaglobal__": ""}},
    }
    surface, _ = tc.compile_js_surface(raw)  # type: ignore[arg-type]
    indices = surface.matcher.Match("boot __betaglobal__ done")
    assert indices is not None
    assert [surface.patterns[i].tech for i in indices] == ["Beta"]
