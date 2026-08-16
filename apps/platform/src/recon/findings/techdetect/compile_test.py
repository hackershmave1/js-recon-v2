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
