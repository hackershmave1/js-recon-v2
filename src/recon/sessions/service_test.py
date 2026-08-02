"""Unit tests for the pure scope-resolution logic of the session service (S3).

The DB-backed create_session / list / mutate paths are covered by the integration
suite (sessions_router_test, app_test); here we pin _resolve_scope_hosts, which is
pure (validation, normalization, default-from-target) and needs no Postgres.
"""

from __future__ import annotations

import pytest

from recon.sessions.service import SessionInvalid, _resolve_scope_hosts


def test_explicit_scope_is_normalized_and_deduped():
    assert _resolve_scope_hosts(["ACME.IO", "cdn.acme.io.", "acme.io"], None) == [
        "acme.io",
        "cdn.acme.io",
    ]


def test_invalid_scope_entry_is_rejected():
    for bad in ["localhost", "com", "10.0.0.1", "github.io", "https://acme.io", "*.acme.io"]:
        with pytest.raises(SessionInvalid, match="invalid scope host"):
            _resolve_scope_hosts([bad], None)


def test_blank_scope_defaults_to_target_host():
    assert _resolve_scope_hosts([], "acme.io") == ["acme.io"]
    assert _resolve_scope_hosts([], "https://acme.io:8443/app.js") == ["acme.io"]
    assert _resolve_scope_hosts([], "ACME.IO") == ["acme.io"]


def test_blank_scope_without_target_stays_empty():
    # An upload session declares no scope; that is allowed (the crawl guard is the
    # security boundary, not this).
    assert _resolve_scope_hosts([], None) == []
    assert _resolve_scope_hosts([], "") == []


def test_blank_scope_does_not_seed_from_an_unusable_target():
    # An IP-literal / localhost / single-label target is not a valid scope entry,
    # so it does NOT silently become the scope; the crawl is refused downstream.
    assert _resolve_scope_hosts([], "10.0.0.1") == []
    assert _resolve_scope_hosts([], "localhost") == []
    assert _resolve_scope_hosts([], "https://169.254.169.254/") == []


def test_explicit_scope_wins_over_target():
    # A declared scope is authoritative; the target only fills a blank one.
    assert _resolve_scope_hosts(["acme.io"], "other.com") == ["acme.io"]
