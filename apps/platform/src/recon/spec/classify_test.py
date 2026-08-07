"""Colocated tests for `recon.spec.classify`: the §5.1/§5.2 canonicalization
core (`compare_key`/`is_partial`/`is_non_http`) plus the §5.3 decision-order
dispatcher (`classify_operation`).

Seeded from the task-5 brief's three required cases, plus the design's own
named risks: §11's cross-style parity ("the compare-key must reduce spec
placeholders, client value-templates, and single-segment `${...}` identically
-- a mismatch silently re-introduces FPs") and §5.2's concatenated-
interpolation example (`${a}${b}`). The `classify_operation` section adds the
task-6 brief's six required cases (every branch + the B2 worked example) plus
the root-path vacuous-suffix edge case resolved during implementation. Pure
unit tests -- no infra.
"""

from __future__ import annotations

from recon.findings import extract, normalize
from recon.spec import classify
from recon.spec.classify import (
    Classification,
    SpecSummary,
    classify_operation,
    compare_key,
    is_non_http,
    is_partial,
    summarize,
)
from recon.spec.ingest import DocumentedOp, ingest_spec

# --- Regex parity (drift guard) -----------------------------------------------


def test_numeric_uuid_regexes_match_normalize():
    """Guard against accidental drift of the _INT_RE and _UUID_RE regexes
    between classify.py and normalize.py (design §11 risk: a mismatch silently
    re-introduces false positives in the shadow diff). Both modules keep
    independent copies to avoid cross-feature coupling; this test ensures they
    stay synchronized."""
    assert classify._INT_RE.pattern == normalize._INT_RE.pattern
    assert classify._UUID_RE.pattern == normalize._UUID_RE.pattern


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
    for method in extract.HTTP_METHODS:
        assert is_non_http(f"{method} /x") is False


def test_is_non_http_wss_and_arbitrary_non_http_verb():
    assert is_non_http("WSS /chat") is True
    assert is_non_http("CONNECT /tunnel") is True


# --- classify_operation (§5.3, gate B2) ---------------------------------------

DOC = [
    DocumentedOp("GET", "/location/address/search"),
    DocumentedOp("POST", "/search"),
    DocumentedOp("GET", "/pets/{petId}"),
]


def test_documented_exact_and_param():
    assert classify_operation("GET /location/address/search", DOC).status == "documented"
    assert classify_operation("GET /pets/${id}", DOC).status == "documented"  # N1


def test_documented_reason_and_matched_operation():
    result = classify_operation("GET /location/address/search", DOC)
    assert result == Classification("documented", "documented", "GET /location/address/search")


def test_non_http_never_shadow():
    expected = Classification("unresolved", "non-http", None)
    assert classify_operation("WS /chat", DOC) == expected


def test_partial_never_shadow():
    assert classify_operation("GET /${API}/pets", DOC).status == "unresolved"


def test_suffix_before_verb_mismatch():
    # B2 worked example: /search is a proper suffix of /location/address/search,
    # so this must NOT fall through to undocumented-method against POST /search.
    result = classify_operation("GET /search", DOC)
    assert result.status == "unresolved"
    assert result.reason == "suffix-verify"
    assert result.matched_operation == "GET /location/address/search"


def test_suffix_matches_in_the_other_direction_too():
    # Same rule, opposite direction: a DOCUMENTED path is the proper suffix of
    # the (longer) client path -- both directions of step 4 must fire.
    result = classify_operation("GET /v2/location/address/search", DOC)
    assert result.status == "unresolved"
    assert result.reason == "suffix-verify"
    assert result.matched_operation == "GET /location/address/search"


def test_undocumented_path_is_shadow():
    result = classify_operation("DELETE /admin/wipe", DOC)
    assert result.status == "shadow"
    assert result.reason == "undocumented-path"
    assert result.matched_operation is None


def test_verb_mismatch_shadow_when_not_suffix():
    # Path matches GET /pets/{petId} (equal segment count, so NOT a proper
    # suffix -- see step 4); only the method differs.
    result = classify_operation("DELETE /pets/9", DOC)
    assert result.status == "shadow"
    assert result.reason == "undocumented-method"
    assert result.matched_operation == "GET /pets/{petId}"


def test_root_path_is_not_a_vacuous_suffix_match():
    # A bare `/` (zero path segments) must NOT satisfy "proper suffix of
    # everything" just because the spec has longer documented paths -- see
    # `_is_proper_suffix`'s non-empty guard.
    result = classify_operation("GET /", DOC)
    assert result.status == "shadow"
    assert result.reason == "undocumented-path"


def test_no_documented_ops_still_shadows_a_complete_path():
    result = classify_operation("GET /anything", [])
    assert result == Classification("shadow", "undocumented-path", None)


# --- summarize (§5.4, gate N7 self-audit) --------------------------------------
#
# The plan's original metric ("shadows whose reason indicates a suffix
# relationship / total shadows") is uncomputable: `classify_operation`'s step 4
# diverts every suffix match to `unresolved` before any `shadow` verdict is
# ever returned (see `test_suffix_before_verb_mismatch` above), so a `status
# == "shadow"` `Classification` can never carry `reason == "suffix-verify"`.
# These tests exercise the controller-redefined `base_url_incompleteness_ratio`
# instead (see `summarize`'s docstring for the full rationale). Inputs are
# `Classification(...)` literals built directly -- `summarize` is a pure
# aggregation over whatever `Classification`s it's handed, never routed
# through `classify_operation`.


def test_summarize_returns_spec_summary_with_expected_fields():
    # Keyword-args pin the exact field names of the frozen dataclass contract,
    # not just their values by position.
    s = summarize([Classification("documented", "documented", "GET /pets")])
    assert s == SpecSummary(
        documented=1,
        shadow=0,
        unresolved=0,
        suffix_verify=0,
        base_url_incompleteness_ratio=0.0,
    )


def test_summary_counts_by_status():
    cs = [
        Classification("documented", "documented", "GET /pets"),
        Classification("documented", "documented", "GET /orders"),
        Classification("shadow", "undocumented-path", None),
        Classification("unresolved", "partial", None),
        Classification("unresolved", "non-http", None),
    ]
    s = summarize(cs)
    assert (s.documented, s.shadow, s.unresolved) == (2, 1, 2)


def test_summary_suffix_verify_requires_both_unresolved_status_and_reason():
    # The one row real `classify_operation` output can never produce
    # (status="shadow", reason="suffix-verify") must NOT count toward
    # `suffix_verify` -- only toward `shadow` -- proving the ratio can't be
    # silently re-broken by matching on `reason` alone.
    cs = [
        Classification("unresolved", "suffix-verify", "GET /a/b"),
        Classification("unresolved", "suffix-verify", "GET /c/d"),
        Classification("unresolved", "partial", None),  # unresolved, wrong reason
        Classification("shadow", "suffix-verify", None),  # unreachable via real classify_operation
    ]
    s = summarize(cs)
    assert s.suffix_verify == 2
    assert s.shadow == 1
    assert s.unresolved == 3


def test_summary_ratio_zero_when_no_shadow_or_suffix_verify():
    cs = [
        Classification("documented", "documented", "GET /pets"),
        Classification("unresolved", "partial", None),
        Classification("unresolved", "non-http", None),
    ]
    s = summarize(cs)
    assert (s.shadow, s.suffix_verify) == (0, 0)
    assert s.base_url_incompleteness_ratio == 0.0


def test_summary_ratio_all_documented_is_zero():
    cs = [
        Classification("documented", "documented", "GET /a"),
        Classification("documented", "documented", "GET /b"),
    ]
    s = summarize(cs)
    assert s.base_url_incompleteness_ratio == 0.0


def test_summary_ratio_computes_expected_fraction():
    cs = [
        Classification("shadow", "undocumented-path", None),
        Classification("shadow", "undocumented-method", "GET /x"),
        Classification("shadow", "undocumented-path", None),
        Classification("unresolved", "suffix-verify", "GET /y"),
    ]
    s = summarize(cs)
    assert (s.shadow, s.suffix_verify) == (3, 1)
    assert s.base_url_incompleteness_ratio == 0.25


# --- ingest -> classify seam (final-review Fix 1 regression) ------------------
#
# `ingest_spec` and `classify_operation` are exercised together, not just each
# in isolation: a REAL OpenAPI 3.x spec almost always gives `servers[].url` as
# a full `scheme://host/basePath` string (design §4 says "prepend the base
# PATH from the spec's servers"), while the client side
# (`normalize.endpoint_operation`, via `urlsplit(url).path`) is host-free by
# construction. If `ingest`'s server-base resolution ever regresses to keeping
# the host, every documented op's compare-key would carry a
# `https:`/`api.example.com`-shaped segment the client side can never produce,
# and the `documented` bucket would silently empty for any spec that uses a
# real, host-ful server URL -- exactly the bug this test pins.


def test_hostful_server_url_documented_op_matches_client_operation():
    ingested = ingest_spec(
        b"openapi: 3.0.0\n"
        b"info: {title: t, version: '1'}\n"
        b"servers: [{url: 'https://api.example.com/v1'}]\n"
        b"paths: {/pets: {get: {responses: {'200': {description: ok}}}}}\n"
    )
    # The documented op's path must be PATH-ONLY (`/v1/pets`), never
    # host-prefixed (`https://api.example.com/v1/pets`).
    assert ("GET", "/v1/pets") in [(o.method, o.path) for o in ingested.documented]

    # What `normalize.endpoint_operation` produces for a real client call to
    # https://api.example.com/v1/pets -- path-only, no host, per its own
    # `urlsplit(url).path` implementation.
    client_operation = normalize.endpoint_operation("GET", "https://api.example.com/v1/pets")
    assert client_operation == "GET /v1/pets"

    result = classify_operation(client_operation, ingested.documented)
    assert result.status == "documented"
