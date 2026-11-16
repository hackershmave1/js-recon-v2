"""Team third-party rules: validation and the built-in + team merge (ADR-0010, R2). No database."""

import pytest

from recon.findings import noise_hosts, team_rules, third_party_rules


def test_host_is_normalized_like_a_scope_entry():
    assert team_rules.validate_host("  *.Tracker.Example.COM. ") == "tracker.example.com"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "com",
        "localhost",
        "10.0.0.1",
        "127.0.0.1",
        "github.io",
        "a b.example.com",
        "https://x.example.com",
        "x.example.com/path",
        "x.example.com:8080",
        "*",
        "a..b.com",
    ],
)
def test_host_that_is_not_a_usable_host_is_rejected(bad):
    with pytest.raises(team_rules.TeamRuleError):
        team_rules.validate_host(bad)


def test_pattern_is_trimmed_and_star_runs_collapsed():
    assert (
        team_rules.validate_pattern("  /vendor/**/chat-widget*.js ") == "/vendor/*/chat-widget*.js"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "*",
        "/*",
        "*.js",  # nothing identifying: would drop every script
        "a*b*c*d*e*f*g",  # too many wildcards: the matcher backtracks
        "x" * (team_rules.MAX_PATTERN_LENGTH + 1),
        "/vendor/chat widget.js",  # whitespace
        "/vendor/\x00chat.js",  # control character
        "https://cdn.example.com/chat.js",  # a pattern is not a URL
    ],
)
def test_unsafe_pattern_is_rejected(bad):
    with pytest.raises(team_rules.TeamRuleError):
        team_rules.validate_pattern(bad)


def test_team_pattern_has_linear_match_time_on_a_hostile_url():
    pattern = team_rules.validate_pattern("/a*a*a*a/chat*.js")
    url = "https://x.example.com/" + "a" * 5000 + "/nope"
    assert third_party_rules.matches_pattern(url, pattern) is False


def test_merged_view_is_builtin_plus_team_and_never_less():
    team = team_rules.TeamRules(
        hosts=frozenset({"tracker.example.com"}), file_patterns=("/vendor/chat*.js",)
    )
    assert team.noise_hosts() >= third_party_rules.HOSTS
    assert "tracker.example.com" in team.noise_hosts()
    assert set(team.file_patterns_all()) >= set(third_party_rules.FILE_PATTERNS)
    assert "/vendor/chat*.js" in team.file_patterns_all()


def test_team_host_is_matched_like_a_built_in_host_exact_or_dot_suffix():
    team = team_rules.TeamRules(hosts=frozenset({"tracker.example.com"}), file_patterns=())
    hosts = team.noise_hosts()
    assert noise_hosts.is_noise_host("cdn.tracker.example.com", denylist=hosts)
    assert noise_hosts.is_noise_host("tracker.example.com", denylist=hosts)
    assert not noise_hosts.is_noise_host("nottracker.example.com", denylist=hosts)
    assert not noise_hosts.is_noise_host("cdn.tracker.example.com")  # built-in alone: unchanged


def test_empty_team_changes_nothing():
    assert team_rules.EMPTY.noise_hosts() == third_party_rules.HOSTS
    assert team_rules.EMPTY.file_patterns_all() == third_party_rules.FILE_PATTERNS
