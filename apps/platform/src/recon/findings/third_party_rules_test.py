"""The built-in third-party rules (ADR-0010): one JSON list, the platform matcher, and the host list
that existed before it moved there."""

from __future__ import annotations

import pytest

from recon.findings import noise_hosts, third_party_rules, visibility
from recon.findings.visibility import THIRD_PARTY, why_hidden


@pytest.mark.parametrize(("url", "expected"), third_party_rules.CASES)
def test_the_shared_cases_match_as_declared(url, expected):
    # The same cases run in the extension's tests (tests/test_third_party_rules.mjs).
    assert third_party_rules.matches_file_pattern(url) is expected


def test_the_existing_noise_host_list_is_unchanged():
    assert len(noise_hosts.DEFAULT_NOISE_HOSTS) == 84
    for host in (
        "google-analytics.com",
        ["cdn", "jsdelivr.net"][1],
        "sentry.io",
        "stripe.com",
        "w3.org",
        "intercom.io",
        "ajax.googleapis.com",
        "code.jquery.com",
    ):
        assert noise_hosts.is_noise_host(host) or noise_hosts.is_noise_host("www." + host), host
    assert noise_hosts.is_noise_host("cdn.mxpnl.com")
    assert not noise_hosts.is_noise_host("notamplitude.com")  # dot-boundary, never a bare suffix
    assert not noise_hosts.is_noise_host(None)


def test_every_pattern_and_host_is_a_clean_entry():
    for host in third_party_rules.HOSTS:
        assert host == host.lower() and "/" not in host and " " not in host
    for pattern in third_party_rules.FILE_PATTERNS:
        assert pattern == pattern.strip() and "/" in pattern  # a file pattern, never a bare host


def test_file_patterns_apply_on_any_host_and_ignore_the_query():
    assert third_party_rules.matches_file_pattern("https://app.acme.io/static/jquery-3.min.js?v=2")
    assert third_party_rules.matches_file_pattern("http://localhost:3000/js/jquery.min.js")
    assert not third_party_rules.matches_file_pattern(
        "https://app.acme.io/static/app.js?x=jquery.min.js"
    )
    assert not third_party_rules.matches_file_pattern(None)
    assert not third_party_rules.matches_file_pattern("not a url")


def test_a_pattern_that_is_only_wildcards_cannot_blow_up():
    assert third_party_rules.matches_pattern("https://a.io/x", "*" * 500) is True
    assert third_party_rules.matches_pattern("https://a.io/x", "") is False


# --- the platform decision: a finding only in third-party FILES is third-party ----------------- #

_FP = ["acme.io"]
_JQ = "https://app.acme.io/static/jquery-3.6.0.min.js"
_APP = "https://app.acme.io/static/app.js"


def _why(finding_type, asset_urls):
    return why_hidden(finding_type, "GET /api/x", [None], _FP, asset_urls=asset_urls)


def test_a_finding_only_in_third_party_files_is_third_party_even_on_the_apps_own_host():
    assert _why("endpoint", [_JQ]) == THIRD_PARTY
    assert _why("endpoint", [_JQ, "https://app.acme.io/wp-includes/js/x.js"]) == THIRD_PARTY


def test_one_app_file_keeps_it_shown():
    assert _why("endpoint", [_JQ, _APP]) is None
    assert _why("endpoint", [_APP]) is None
    assert _why("endpoint", []) is None
    assert _why("endpoint", [None]) is None


def test_a_confirmed_secret_in_a_library_file_is_never_hidden():
    assert _why("secret", [_JQ]) is None


def test_the_rule_does_not_need_a_scope():
    assert why_hidden("endpoint", "GET /x", [None], None, asset_urls=[_JQ]) == THIRD_PARTY


def test_visibility_module_documents_the_file_rule():
    assert "third_party_rules" in (visibility.__doc__ or "")
