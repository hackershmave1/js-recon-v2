"""Unit tests for the default analytics/telemetry/vendor noise-host filter (#3)."""

from __future__ import annotations

from recon.findings.noise_hosts import is_all_noise, is_noise_host


def test_exact_and_dot_suffix_match() -> None:
    assert is_noise_host("amplitude.com")
    assert is_noise_host("api.eu.amplitude.com")  # dot-suffix of amplitude.com
    assert is_noise_host("sentry.io")
    assert is_noise_host("o123.ingest.sentry.io")
    assert is_noise_host("GOOGLE-ANALYTICS.COM")  # case-insensitive
    assert is_noise_host("js.stripe.com.")  # trailing dot tolerated


def test_real_and_hostless_are_not_noise() -> None:
    assert not is_noise_host("hackerone.com")
    assert not is_noise_host("api.hackerone.com")
    assert not is_noise_host("notamplitude.com")  # NOT a dot-suffix — never false-matches
    assert not is_noise_host("amplitude.com.evil.test")  # suffix guard: evil.test is the real host
    assert not is_noise_host(None)
    assert not is_noise_host("")


def test_is_all_noise_drops_only_all_noise_findings() -> None:
    # A finding is hidden only when it HAS a host and EVERY host is noise.
    assert is_all_noise({"api.amplitude.com"})  # all noise -> hide
    assert is_all_noise({"amplitude.com", "sentry.io"})  # all noise -> hide
    assert not is_all_noise(set())  # relative/host-less finding -> keep
    assert not is_all_noise({None})  # host-less occurrence -> keep
    assert not is_all_noise({"api.hackerone.com"})  # real host -> keep
    assert not is_all_noise({"amplitude.com", "api.hackerone.com"})  # mixed -> keep the surface
