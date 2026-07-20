"""Colocated tests for REQ-D3 finding-hash normalization.

Seeded from the spec's §8 test-vector table plus the defects the adversarial
design review demanded coverage for (C1 host, C2/H1 over-merge, H2/H3 paths,
M1/M2 secrets). Pure unit tests — no infra.
"""

from __future__ import annotations

import hashlib

from recon.findings import normalize as nz


# --- entropy -----------------------------------------------------------------

def test_shannon_entropy_empty_is_zero():
    assert nz.shannon_entropy("") == 0.0


def test_shannon_entropy_orders_slug_below_token():
    # A human slug is lower entropy than a real base64/hex build hash.
    assert nz.shannon_entropy("acmecorporationholdings") < nz.shannon_entropy(
        "aZ9kQ2mB7xL4wP0rT6uY1eC5"
    )


# --- path-segment templating (§4.1) -----------------------------------------

def test_template_segment_numeric_and_uuid_and_hex():
    assert nz.template_segment("4821") == "{id}"
    assert nz.template_segment("f47ac10b-58cc-4372-a567-0e02b2c3d479") == "{uuid}"
    assert nz.template_segment("a1b2c3d4e5f60718") == "{hash}"  # 16 hex


def test_template_segment_keeps_versions_and_low_entropy_slugs_literal():
    assert nz.template_segment("v1") == "v1"
    assert nz.template_segment("v2") == "v2"
    # long, low-entropy human slug must NOT collapse (review H1 over-merge)
    slug = "acme-corporation-holdings-emea"
    assert nz.template_segment(slug) == slug


def test_template_segment_collapses_high_entropy_token():
    token = "aZ9kQ2mB7xL4wP0rT6uY1eC5"  # >=24 chars, high entropy
    assert nz.template_segment(token) == "{hash}"


# --- source-path normalization (§3) -----------------------------------------

def test_source_path_strips_scheme_keeps_webpack_namespace():
    assert nz.normalize_source_path("webpack://app/src/api/users.js") == "app/src/api/users.js"


def test_source_path_handles_single_slash_sourcemapper_form():
    # sourcemapper writes `webpack:/js/client.<hash>.js` (single slash, review H3)
    assert nz.normalize_source_path("webpack:/js/client.356c14916fb23f85.js") == (
        "js/client.{hash}.js"
    )


def test_source_path_lowercases_http_host_only():
    assert nz.normalize_source_path("https://Cdn.X.com/Static/App.js") == (
        "cdn.x.com/Static/App.js"
    )


def test_source_path_is_stable_across_rebuild_content_hashes():
    a = nz.normalize_source_path("webpack://app/js/app.9f8e7d6c.js")
    b = nz.normalize_source_path("webpack://app/js/app.1a2b3c4d.js")
    assert a == b == "app/js/app.{hash}.js"


def test_source_path_collapses_short_contenthash_6():
    # webpack `[contenthash:6]` -> 6 hex chars, must still collapse (review H3)
    assert nz.normalize_source_path("app.a1b2c3.js") == "app.{hash}.js"


def test_source_path_resolves_dot_segments():
    assert nz.normalize_source_path("app/src/../lib/./x.js") == "app/lib/x.js"


def test_source_path_null_vs_empty_are_distinct_from_no_map():
    assert nz.normalize_source_path(None) == nz.NULL_SOURCE
    assert nz.normalize_source_path("   ") == nz.NULL_SOURCE
    assert nz.NULL_SOURCE != nz.NO_MAP


# --- endpoint normalization (§4.1) ------------------------------------------

def test_endpoint_templates_path_and_sorts_query_keys():
    ep = nz.normalize_endpoint(
        "post",
        "https://API.acme.io:443/users/4821/orders/"
        "f47ac10b-58cc-4372-a567-0e02b2c3d479?sort=asc&page=2&sort=desc",
    )
    assert ep.value == "POST /users/{id}/orders/{uuid}?page&sort"
    assert ep.host == "api.acme.io"  # host is occurrence-only, lowercased, port dropped


def test_endpoint_host_not_in_value_so_two_hosts_share_identity():
    # C1: relative and absolute-to-different-host calls to the same path collapse.
    a = nz.normalize_endpoint("GET", "https://a.example.com/users/1")
    b = nz.normalize_endpoint("GET", "https://b.example.com/users/2")
    rel = nz.normalize_endpoint("GET", "/users/9")
    assert a.value == b.value == rel.value == "GET /users/{id}"
    assert {a.host, b.host, rel.host} == {"a.example.com", "b.example.com", None}


def test_endpoint_method_distinguishes_identity():
    get = nz.normalize_endpoint("GET", "/x")
    post = nz.normalize_endpoint("POST", "/x")
    assert get.value != post.value


def test_endpoint_strips_trailing_slash():
    assert nz.normalize_endpoint("GET", "/a/b/").value == "GET /a/b"
    assert nz.normalize_endpoint("GET", "/").value == "GET /"


def test_endpoint_dedupes_array_query_keys():
    ep = nz.normalize_endpoint("GET", "/search?ids[]=1&ids[]=2&q=hi")
    assert ep.value == "GET /search?ids&q"


def test_endpoint_websocket():
    ep = nz.normalize_endpoint("WSS", "wss://rt.acme.io/socket/42")
    assert ep.value == "WSS /socket/{id}"
    assert ep.host == "rt.acme.io"


# --- param normalization (§4.3) ---------------------------------------------

def test_param_value_binds_to_operation():
    op = nz.endpoint_operation("post", "https://api.x/login")
    assert op == "POST /login"
    assert nz.normalize_param_value(op, "body", "token") == "POST /login body:token"


# --- secret normalization (§4.2) --------------------------------------------

def test_secret_strips_trailing_delimiter_for_stable_hash():
    # M2: engine v2 captures a trailing quote; identity must not churn.
    clean = nz.normalize_secret_value("sk_live_ABC", "stripe.live_secret_key")
    quoted = nz.normalize_secret_value('sk_live_ABC"', "stripe.live_secret_key")
    expected = "stripe:" + hashlib.sha256(b"sk_live_ABC").hexdigest()
    assert clean == quoted == expected


def test_secret_provider_falls_back_to_leading_token():
    assert nz.provider_for_rule("stripe.live_restricted_key") == "stripe"
    assert nz.provider_for_rule("some_new.rule.v3") == "some"


def test_secret_does_not_strip_token_legal_chars():
    # `.` `-` `_` `=` `+` `/` are legal inside tokens and must survive.
    token = "abc.d0-Ef_G/h+i="
    assert nz.strip_secret_delimiters(token) == token


# --- hashing (§5) ------------------------------------------------------------

def test_finding_hash_ignores_volatile_fields_by_construction():
    # Only (type, value, path) are inputs, so two sightings differing solely in
    # col/evidence produce the same hash (REQ-D3 retry idempotency).
    h1 = nz.finding_hash("endpoint", "GET /users/{id}", "app/src/api.js")
    h2 = nz.finding_hash("endpoint", "GET /users/{id}", "app/src/api.js")
    assert h1 == h2
    assert len(h1) == 64 and int(h1, 16) >= 0  # 64-char hex


def test_finding_hash_no_cross_type_collision():
    same_value = "stripe:deadbeef"
    assert nz.finding_hash("secret", same_value, "p") != nz.finding_hash(
        "param", same_value, "p"
    )


def test_finding_hash_changes_with_path_when_scoped():
    a = nz.finding_hash("secret", "stripe:x", "app/a.js")
    b = nz.finding_hash("secret", "stripe:x", "app/b.js")
    assert a != b  # path-scoped identity (locked decision)


def test_occurrence_hash_is_deterministic_and_offset_sensitive():
    base = dict(host="api.x", raw_url="/users/1", path="app/a.js", start=10, end=20)
    assert nz.occurrence_hash(**base) == nz.occurrence_hash(**base)
    moved = {**base, "start": 99, "end": 109}
    assert nz.occurrence_hash(**base) != nz.occurrence_hash(**moved)


def test_occurrence_hash_tolerates_none_and_bytes():  # review LOW-4
    # Must not raise on None or non-JSON-native types (default=str).
    h = nz.occurrence_hash(host=None, raw=b"\x00secret", start=1, end=2)
    assert len(h) == 64


# --- regressions from the code review -----------------------------------------

def test_source_path_protects_camelcase_stems_from_collapse():  # review HIGH-1
    a = nz.normalize_source_path("app/src/Base64Encoder.js")
    b = nz.normalize_source_path("app/src/Utf8Decoder.js")
    assert a == "app/src/Base64Encoder.js"
    assert b == "app/src/Utf8Decoder.js"
    assert a != b  # distinct files must not merge into {hash}.js


def test_source_path_collapses_base64url_hash_regardless_of_class():  # review MEDIUM-2
    # interior hash component collapses whether or not it has upper/digit
    assert nz.normalize_source_path("app/index.q7x2m9k4.js") == "app/index.{hash}.js"
    assert nz.normalize_source_path("app/index.kLmnoPqr.js") == "app/index.{hash}.js"


def test_source_path_collapses_pure_hex_stem():
    assert nz.normalize_source_path("9f8e7d6c.js") == "{hash}.js"


def test_source_path_dotdot_never_pops_the_authority():  # review LOW-6
    assert nz.normalize_source_path("webpack://app/../secret.js") == "app/secret.js"


def test_endpoint_does_not_decode_encoded_slash():  # security: no forged segment
    ep = nz.normalize_endpoint("GET", "/a%2Fb/12")
    assert ep.value == "GET /a%2Fb/{id}"
    assert "%2F" in ep.value


def test_endpoint_drops_fragment_and_empty_query_key():  # review LOW-6
    assert nz.normalize_endpoint("GET", "/a#frag").value == "GET /a"
    assert nz.normalize_endpoint("GET", "/a?=v&x=1").value == "GET /a?x"


def test_finding_hash_golden_vector_locks_cross_process_stability():
    # Hardcoded digest — any change to the canonical form or algorithm breaks this.
    assert nz.finding_hash("endpoint", "GET /users/{id}", "app/src/api.js") == (
        "f47b2e5c384f0deeeafb61cfa39210339c7e237ef99ed002b527fe5fa9788046"
    )
