"""The default-visibility rule, pure (no stack). The page-route cases are the 23 real values a
Next.js banking capture produced (run 7e95968a): only the app's own `/` is meaningful."""

from __future__ import annotations

import pytest

from recon.findings.visibility import (
    JUNK_OR_DYNAMIC,
    NOISE,
    OFF_SCOPE,
    expand_scope_for_display,
    why_hidden,
)

FIRST_PARTY = expand_scope_for_display(["banking.crossriver.com"])
ROUTE = "page_route"


def _route(value: str, host: str | None = None, first_party=FIRST_PARTY) -> str | None:
    return why_hidden(ROUTE, value, [host], first_party)


@pytest.mark.parametrize(
    "value",
    [
        "http://[${e}]",
        "https://${e}",
        "http://n",
        "https://a",
        "https://a#б",
        "https://a/c%20d?a=1&c=3",
        "https://a@b",
        "https://x",
        "https://тест",
        "http://localhost:3000",
        "https://[a-z]/x",  # a bracketed non-IP host must not crash any surface
        "https://[a-z]+/x",
    ],
)
def test_junk_and_dynamic_urls_are_hidden(value):
    assert _route(value) == JUNK_OR_DYNAMIC


@pytest.mark.parametrize(
    ("value", "host"),
    [
        ("https://cdn.mxpnl.com", "cdn.mxpnl.com"),
        ("https://cdn.mxpnl.com/libs/", "cdn.mxpnl.com"),
        ("https://api-js.mixpanel.com", "api-js.mixpanel.com"),
    ],
)
def test_analytics_vendors_are_noise(value, host):
    assert _route(value, host) == NOISE


@pytest.mark.parametrize(
    "value",
    [
        "http://json-schema.org/draft-04/schema#",
        "https://json-schema.org/draft/2020-12/schema",
        "https://github.com/zloirock/core-js",
        "https://errors.authjs.dev#${this.type.toLowerCase()}",
        "https://safe-redirect.invalid",
    ],
)
def test_third_party_page_urls_are_off_scope(value):
    assert _route(value) == OFF_SCOPE


@pytest.mark.parametrize(
    "value",
    [
        "/",
        "/dashboard",
        "https://banking.crossriver.com/login",
        "https://www.crossriver.com/about",  # apex-expanded sibling of the declared host
        "https://banking.crossriver.com/accounts/${id}",  # a template in the PATH keeps its host
        "https://banking.crossriver.com/x?next=${u}",
    ],
)
def test_first_party_routes_are_shown(value):
    assert _route(value) is None


def test_with_no_first_party_knowledge_only_junk_is_hidden():
    assert _route("https://github.com/x", first_party=None) is None
    assert _route("https://x", first_party=None) == JUNK_OR_DYNAMIC


def test_a_local_lab_target_in_scope_is_shown():
    local = expand_scope_for_display(["localhost"], allow_local=True)
    assert why_hidden(ROUTE, "http://localhost:3000/app", [None], local, allow_local=True) is None


# #240 (a deliberate reversal of "the scope rule never hides an API call"): an endpoint whose EVERY
# occurrence is on a real out-of-scope host is the third party's surface, not the target's, so it
# is hidden like an off-scope page route. Reversible (`include_hidden`); an occurrence the app
# itself makes (in scope, or relative = its own origin) keeps the whole finding shown.
_ENDPOINT_TYPES = ["endpoint", "endpoint_suspected", "endpoint_unresolved", "endpoint_generic"]


@pytest.mark.parametrize("finding_type", _ENDPOINT_TYPES)
def test_an_endpoint_only_on_out_of_scope_hosts_is_off_scope(finding_type):
    assert why_hidden(finding_type, "/api/oembed.json", ["vimeo.com"], FIRST_PARTY) == OFF_SCOPE
    assert (
        why_hidden(finding_type, "/api/x", ["vimeo.com", "plausible.io"], FIRST_PARTY) == OFF_SCOPE
    )


@pytest.mark.parametrize("finding_type", _ENDPOINT_TYPES)
def test_one_in_scope_occurrence_keeps_the_whole_endpoint_shown(finding_type):
    hosts = ["banking.crossriver.com", "plausible.io"]
    assert why_hidden(finding_type, "/api/event", hosts, FIRST_PARTY) is None


@pytest.mark.parametrize("finding_type", _ENDPOINT_TYPES)
def test_a_relative_call_is_the_apps_own_origin_and_stays_shown(finding_type):
    assert why_hidden(finding_type, "/api/x", [None], FIRST_PARTY) is None
    assert why_hidden(finding_type, "/api/x", ["vimeo.com", None], FIRST_PARTY) is None


def test_an_endpoint_is_not_off_scope_when_nothing_is_known_about_scope():
    assert why_hidden("endpoint_suspected", "/api/x", ["vimeo.com"], None) is None


def test_a_secret_or_param_is_never_hidden_by_the_endpoint_scope_rule():
    assert why_hidden("secret", "aws:sha256:ab", ["vimeo.com"], FIRST_PARTY) is None
    assert why_hidden("param", "q", ["vimeo.com"], FIRST_PARTY) is None


def test_an_endpoint_on_a_dynamic_host_is_left_to_the_junk_rules_not_off_scope():
    assert why_hidden("endpoint", "GET https://x", ["x"], FIRST_PARTY) is None


def test_analytics_noise_still_wins_for_an_endpoint():
    assert (
        why_hidden(
            "endpoint", "GET https://api.mixpanel.com/track", ["api.mixpanel.com"], FIRST_PARTY
        )
        == NOISE
    )


def test_protocol_relative_routes_are_judged_like_absolute_ones():
    assert _route("//evil.com/x") == OFF_SCOPE
    assert _route("//banking.crossriver.com/x") is None


def test_a_target_on_the_vendor_list_keeps_its_own_surface():
    own = expand_scope_for_display(["mixpanel.com"])
    assert why_hidden("endpoint", "GET /track", ["api.mixpanel.com"], own) is None
    assert why_hidden("endpoint", "GET /track", ["api.mixpanel.com"], FIRST_PARTY) == NOISE
