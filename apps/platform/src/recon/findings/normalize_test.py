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


# --- suspected-endpoint promotion (endpoint-model rework) -------------------


def test_suspected_promotes_relative_path_with_static_segment():
    ep = nz.normalize_suspected_endpoint("GET", "/inbox/subjects")
    assert ep is not None
    assert ep.value == "GET /inbox/subjects"
    assert ep.host is None


def test_suspected_keeps_placeholders_when_one_segment_is_static():
    # >=1 static segment (`inbox_views`) promotes; the `:holder`/`${...}` placeholders survive.
    a = nz.normalize_suspected_endpoint("GET", "/:handle/inbox_views")
    b = nz.normalize_suspected_endpoint("GET", "/${this.props.handle}/inbox_views")
    assert a is not None and a.value == "GET /:handle/inbox_views"
    assert b is not None and b.value == "GET /${this.props.handle}/inbox_views"


def test_suspected_rejects_all_placeholder_path():
    # Pure junk: every segment is a placeholder -> not an endpoint (stays unconfirmed).
    assert nz.normalize_suspected_endpoint("GET", "${e.id}/${t}") is None
    assert nz.normalize_suspected_endpoint("GET", "/:a/:b") is None
    assert nz.normalize_suspected_endpoint("GET", "EXPR") is None


def test_suspected_splits_host_from_full_url_path():
    # A full URL promotes to its templated PATH; the host lands on the occurrence attribute.
    ep = nz.normalize_suspected_endpoint(
        "GET", "https://api.hackerone.com/v1/organizations/${id}/scim_v2"
    )
    assert ep is not None
    assert ep.value == "GET /v1/organizations/{id}/scim_v2"  # v3: positional, name is evidence
    assert ep.host == "api.hackerone.com"


def test_suspected_guards_protocol_relative_authority_swallow():
    # A leading `//` must NOT eat the first path segment as a host (urlsplit's trap).
    ep = nz.normalize_suspected_endpoint("GET", "//foo/bar/list")
    assert ep is not None
    assert ep.value == "GET /foo/bar/list"  # `foo` stays a path segment, not a host
    assert ep.host is None


def test_suspected_keeps_partial_query_and_uppercases_method():
    ep = nz.normalize_suspected_endpoint("post", "/bugs/count?${e.queryString}")
    assert ep is not None
    # v3: a runtime-built query has no knowable keys -> dropped from identity, flagged dynamic.
    assert ep.value == "POST /bugs/count" and ep.dynamic_query


def test_suspected_templates_concrete_id_segments():
    # A concrete numeric segment still templates (shared identity with the confirmed lane).
    ep = nz.normalize_suspected_endpoint("GET", "/users/4821/profile")
    assert ep is not None
    assert ep.value == "GET /users/{id}/profile"


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


def test_secret_provider_for_kingfisher_rule_id_is_second_segment():
    # Kingfisher ids are `kingfisher.<provider>.<n>`; the provider is segment 2,
    # not the leading `kingfisher` engine token.
    assert nz.provider_for_rule("kingfisher.stripe.2") == "stripe"
    assert nz.provider_for_rule("kingfisher.aws.1") == "aws"
    assert nz.provider_for_rule("KINGFISHER.GITHUB.7") == "github"
    assert nz.provider_for_rule("kingfisher") == "unknown"


def test_secret_provider_pins_standalone_akia_rule_to_aws():
    # The shipped standalone-AKIA rule id's first segment is "custom", so it would
    # otherwise hash as "custom:…"; the pinned override keeps a lone AWS access key
    # id's identity as aws:sha256(token), identical to a built-in AWS secret.
    assert nz.provider_for_rule("custom.aws.akia_standalone") == "aws"


def test_secret_provider_pins_config_guid_rule_to_config():
    # The config-GUID rule (DEBT D33) would otherwise derive "custom"; pinned to
    # "config" — NOT "azure", which would be a false claim on the non-Azure `*_KEY`
    # GUIDs the rule also catches (provider prefixes the user-visible finding.value).
    assert nz.provider_for_rule("custom.config.guid_assignment") == "config"


def test_secret_does_not_strip_token_legal_chars():
    # `.` `-` `_` `=` `+` `/` are legal inside tokens and must survive.
    token = "abc.d0-Ef_G/h+i="
    assert nz.strip_secret_delimiters(token) == token


# --- hashing (§5) ------------------------------------------------------------


def test_finding_hash_ignores_volatile_fields_by_construction():
    # Only (version, type, value) are inputs, so two sightings differing solely in
    # col/evidence/path produce the same hash (REQ-D3 retry idempotency).
    h1 = nz.finding_hash("endpoint", "GET /users/{id}")
    h2 = nz.finding_hash("endpoint", "GET /users/{id}")
    assert h1 == h2
    assert len(h1) == 64 and int(h1, 16) >= 0  # 64-char hex


def test_finding_hash_no_cross_type_collision():
    same_value = "stripe:deadbeef"
    assert nz.finding_hash("secret", same_value) != nz.finding_hash("param", same_value)


def test_finding_hash_is_path_independent_v2():
    # v2 (Starbucks-QA dedup): source path is NOT in identity — the same logical
    # finding seen in different files is ONE finding (path lives on occurrences,
    # the C1 treatment applied to path). Was the inverse under v1.
    assert nz.finding_hash("secret", "stripe:x") == nz.finding_hash("secret", "stripe:x")
    assert nz.finding_hash("endpoint", "GET /users/{id}") == nz.finding_hash(
        "endpoint", "GET /users/{id}"
    )


def test_occurrence_hash_is_deterministic_and_offset_sensitive():
    base = {"host": "api.x", "raw_url": "/users/1", "path": "app/a.js", "start": 10, "end": 20}
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
    # Hardcoded digest — any change to the canonical form or algorithm (incl. a
    # FINDING_HASH_VERSION bump) breaks this by design. v3 = sha256 over
    # {"type":"endpoint","v":3,"value":"GET /users/{id}"}.
    assert nz.finding_hash("endpoint", "GET /users/{id}") == (
        "9047a89918ccab9e6cb8af29594a8a9ddcab5340315ab6e4ccce223d8276c520"
    )


# --- operation-key parsers (inverse of builders) ----------------------------


def test_operation_of_endpoint_value_strips_query():
    assert nz.operation_of_endpoint_value("POST /api/users/{id}?a&b") == "POST /api/users/{id}"


def test_operation_of_endpoint_value_without_query():
    assert nz.operation_of_endpoint_value("GET /orders") == "GET /orders"


def test_operation_of_param_value_strips_location_and_name():
    assert nz.operation_of_param_value("POST /api/users/{id} body:name") == "POST /api/users/{id}"


def test_operation_helpers_roundtrip_from_builders():
    operation = nz.endpoint_operation("POST", "https://api.acme.io/api/users/42")
    endpoint_value = nz.normalize_endpoint("POST", "https://api.acme.io/api/users/42?a=1").value
    param_value = nz.normalize_param_value(operation, "body", "name")
    assert nz.operation_of_endpoint_value(endpoint_value) == operation
    assert nz.operation_of_param_value(param_value) == operation


# --- GraphQL value normalization ---------------------------------------------


def test_graphql_named_operation_value():
    assert nz.normalize_graphql_value("mutation", "CreateApiToken") == "mutation CreateApiToken"


def test_graphql_fragment_value_includes_on_type():
    assert (
        nz.normalize_graphql_value("fragment", "UserFields", on_type="User")
        == "fragment UserFields on User"
    )


def test_graphql_anonymous_ops_differ_by_body_digest():
    # A spread-only anonymous op has empty top-level fields; the body digest keeps identity apart.
    a = nz.normalize_graphql_value("query", None, body_digest="aaaaaaaaaaaa")
    b = nz.normalize_graphql_value("query", None, body_digest="bbbbbbbbbbbb")
    assert a == "query · aaaaaaaaaaaa"
    assert a != b


def test_graphql_value_feeds_stable_path_free_finding_hash():
    value = nz.normalize_graphql_value("query", "Me")
    assert nz.finding_hash("graphql", value) == nz.finding_hash("graphql", value)
    assert nz.finding_hash("graphql", value) != nz.finding_hash("graphql", "mutation Me")


# --- v3: placeholder identity (positional {id}, names as evidence) ------------


def test_v3_mapped_and_minified_names_share_one_endpoint_identity():
    mapped = nz.normalize_endpoint("GET", "https://api.acme.com/api/v1/users/${userId}")
    minified = nz.normalize_endpoint("GET", "https://api.acme.com/api/v1/users/${e}")
    assert mapped.value == minified.value == "GET /api/v1/users/{id}"
    assert mapped.path_params == (nz.PathParam(position=1, name="userId", integer=False),)
    assert minified.path_params == (nz.PathParam(position=1, name="e", integer=False),)


def test_v3_integer_literal_and_template_merge_and_record_the_literal():
    literal = nz.normalize_endpoint("GET", "/users/42/orders/${o.id}")
    assert literal.value == "GET /users/{id}/orders/{id}"
    assert literal.path_params == (
        nz.PathParam(position=1, name=None, integer=True),
        nz.PathParam(position=2, name="id", integer=False),
    )


def test_v3_uuid_and_hash_segments_count_in_the_ordinal():
    ep = nz.normalize_endpoint("GET", "/o/f47ac10b-58cc-4372-a567-0e02b2c3d479/items/${itemId}")
    assert ep.value == "GET /o/{uuid}/items/{id}"
    assert [p.position for p in ep.path_params] == [1, 2]
    assert ep.path_params[1].name == "itemId"


def test_v3_leading_and_partial_placeholders_stay_verbatim():
    # A leading placeholder is an unresolved base, a partial one is shape: neither is a param
    # (spec classify / capture correlation key on the literal `${`).
    ep = nz.normalize_endpoint("GET", "/${apiBase}/users/${slug}")
    assert ep.value == "GET /${apiBase}/users/{id}"
    assert ep.path_params == (nz.PathParam(position=1, name="slug", integer=False),)
    partial = nz.normalize_endpoint("POST", "/api${CREATE_PATH}/x/file-${n}.json")
    assert partial.value == "POST /api${CREATE_PATH}/x/file-${n}.json"
    assert partial.path_params == ()


def test_v3_a_slash_or_question_mark_inside_a_substitution_never_splits_the_url():
    ep = nz.normalize_endpoint("GET", '/api/items/${ids.join("/")}')
    assert ep.value == "GET /api/items/{id}"
    tern = nz.normalize_endpoint("GET", '/api/items/${id}${q ? `?${q}` : ""}')
    assert tern.value == 'GET /api/items/${id}${q ? `?${q}` : ""}'
    assert not tern.dynamic_query


def test_v3_placeholder_query_is_dropped_and_marked_dynamic():
    ep = nz.normalize_endpoint("POST", "/${m}?${new URLSearchParams(r)}")
    assert ep.value == "POST /${m}"
    assert ep.dynamic_query
    mixed = nz.normalize_endpoint("GET", "/search?q=1&${extra}")
    assert mixed.value == "GET /search?q" and mixed.dynamic_query


def test_v3_endpoint_operation_matches_the_endpoint_value():
    url = "/users/${userId}?page=2"
    assert nz.endpoint_operation("get", url) == "GET /users/{id}"


def test_v3_suspected_lane_positional_tokens_for_holders_and_templates():
    a = nz.normalize_suspected_endpoint("GET", "/getUiSessionId/${Be}")
    b = nz.normalize_suspected_endpoint("GET", "/getUiSessionId/:Be")
    c = nz.normalize_suspected_endpoint("GET", "/getUiSessionId/EXPR")
    assert a is not None and b is not None and c is not None
    assert a.value == b.value == c.value == "GET /getUiSessionId/{id}"
    lead = nz.normalize_suspected_endpoint("GET", "/:handle/inbox_views")
    assert lead is not None and lead.value == "GET /:handle/inbox_views"


def test_v3_placeholder_name_reads_the_identifier_never_invents_one():
    assert nz.placeholder_name("${userId}") == "userId"
    assert nz.placeholder_name("${item.DSStoragePath}") == "DSStoragePath"
    assert nz.placeholder_name("${ encodeURIComponent(sessionId) }") == "sessionId"
    assert nz.placeholder_name("${String(t.id)}") == "id"
    assert nz.placeholder_name("${e}") == "e"
    assert nz.placeholder_name("${a.b()}") is None
    assert nz.placeholder_name("${x ? y : z}") is None


def test_v3_display_name_prefers_readable_then_longest():
    assert nz.display_param_name(["e", "userId", "uid"]) == "userId"
    assert nz.display_param_name(["t", "e"]) is None
    assert nz.display_param_name([]) is None


def test_v3_rewrite_maps_a_stored_v2_value_to_its_v3_value():
    assert (
        nz.rewrite_v2_value("endpoint", "GET /api/v1/users/${userId}") == "GET /api/v1/users/{id}"
    )
    assert nz.rewrite_v2_value("endpoint", "POST /${m}?${new URLSearchParams(r)}") == "POST /${m}"
    assert nz.rewrite_v2_value("endpoint_suspected", "GET /x/:Be") == "GET /x/{id}"
    assert nz.rewrite_v2_value("param", "GET /u/${userId} query:page") == "GET /u/{id} query:page"
    assert nz.rewrite_v2_value("secret", "aws:abc") == "aws:abc"
    assert nz.rewrite_v2_value("param", "POST /x query:${new URLSearchParams(r)}") is None


def test_v3_display_value_names_each_position_from_its_evidence():
    evidence = [
        {"position": 1, "names": ["e", "orgId"], "integer_only": False},
        {"position": 2, "names": ["t"], "integer_only": False},
    ]
    value = "GET /orgs/{id}/users/{id}?page"
    assert nz.display_endpoint_value(value, evidence) == "GET /orgs/{orgId}/users/{id}?page"
    assert nz.display_endpoint_value(value, None) == value
    assert nz.display_endpoint_value("GET /${b}/x/{id}", [evidence[0]]) == "GET /${b}/x/{orgId}"


def test_v3_merge_path_param_evidence_is_order_independent():
    a = [{"position": 1, "names": [], "integer_only": True}]
    b = [{"position": 1, "names": ["userId"], "integer_only": False}]
    assert (
        nz.merge_path_param_evidence(a, b)
        == nz.merge_path_param_evidence(b, a)
        == [{"position": 1, "names": ["userId"], "integer_only": False}]
    )


def test_v3_current_hash_translates_an_older_run_and_leaves_current_rows_alone():
    import json

    v2 = hashlib.sha256(
        json.dumps(
            {"v": 2, "type": "endpoint", "value": "GET /u/${userId}"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    current = nz.finding_hash("endpoint", "GET /u/{id}")
    assert nz.current_finding_hash("endpoint", "GET /u/${userId}", v2) == current
    assert nz.current_finding_hash("endpoint", "GET /u/{id}", current) == current
