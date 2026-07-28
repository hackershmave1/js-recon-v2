"""Colocated tests for the §5.1/§5.2 canonicalization core (`recon.spec.classify`).

Seeded from the task-5 brief's three required cases, plus the design's own
named risks: §11's cross-style parity ("the compare-key must reduce spec
placeholders, client value-templates, and single-segment `${...}` identically
-- a mismatch silently re-introduces FPs") and §5.2's concatenated-
interpolation example (`${a}${b}`). Pure unit tests -- no infra.
"""

from __future__ import annotations

from recon.spec.classify import compare_key, is_non_http, is_partial


# --- compare_key (§5.1) -------------------------------------------------------

def test_compare_key_wildcards_all_param_styles():
    assert compare_key("GET /pets/{id}") == "GET /pets/*"
    assert compare_key("GET /pets/{petId}") == "GET /pets/*"
    assert compare_key("GET /pets/${id}") == "GET /pets/*"
    assert compare_key("GET /pets/123?x=1") == "GET /pets/*"  # query stripped, numeric wildcarded


def test_compare_key_wildcards_uuid_segment():
    assert compare_key("GET /pets/f47ac10b-58cc-4372-a567-0e02b2c3d479") == "GET /pets/*"


def test_compare_key_keeps_literal_segments_and_wildcards_multiple_params():
    assert compare_key("GET /orgs/{orgId}/pets/{petId}") == "GET /orgs/*/pets/*"


def test_compare_key_strips_query_without_touching_literal_path():
    assert compare_key("GET /pets?x=1&y=2") == "GET /pets"


def test_compare_key_root_path_is_unchanged():
    assert compare_key("GET /") == "GET /"


def test_compare_key_parity_across_param_spellings():
    # design §11's core risk: a spec placeholder, a `normalize` value-template,
    # and a client interpolation must reduce IDENTICALLY or the diff silently
    # re-introduces false positives.
    keys = {
        compare_key("GET /pets/{petId}"),
        compare_key("GET /pets/{id}"),
        compare_key("GET /pets/${id}"),
    }
    assert keys == {"GET /pets/*"}


# --- is_partial (§5.2) --------------------------------------------------------

def test_is_partial():
    assert is_partial("GET /${API}/pets") is True  # leading interpolation
    assert is_partial("GET /v${n}/pets") is True  # mixed segment
    assert is_partial("GET /pets/${id}") is False  # single-segment param -> matchable
    assert is_partial("GET /pets/{id}") is False


def test_is_partial_mixed_segment_in_non_leading_position():
    assert is_partial("GET /pets/v${n}") is True


def test_is_partial_concatenated_interpolations_is_mixed():
    # design §5.2's own example of a segment that "mixes ... interpolation":
    # two substitutions filling one segment isn't one clean, wildcardable param.
    assert is_partial("GET /${a}${b}/pets") is True


def test_is_partial_numeric_and_uuid_segments_are_never_partial():
    assert is_partial("GET /pets/123") is False
    assert is_partial("GET /pets/f47ac10b-58cc-4372-a567-0e02b2c3d479") is False


def test_is_partial_all_literal_path_is_not_partial():
    assert is_partial("GET /pets/available") is False


# --- is_non_http (§5.1, gate B3) ---------------------------------------------

def test_is_non_http():
    assert is_non_http("WS /chat") is True
    assert is_non_http("GET /chat") is False


def test_is_non_http_recognizes_every_documented_http_method():
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        assert is_non_http(f"{method} /x") is False


def test_is_non_http_wss_and_arbitrary_non_http_verb():
    assert is_non_http("WSS /chat") is True
    assert is_non_http("CONNECT /tunnel") is True
