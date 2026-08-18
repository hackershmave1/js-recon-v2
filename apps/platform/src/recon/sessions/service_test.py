"""Unit tests for the pure logic of the session service (S3).

The DB-backed create_session / list / mutate paths are covered by the integration
suite (sessions_router_test, app_test); here we pin the pure pieces that need no
Postgres: _resolve_scope_hosts (validation, normalization, default-from-target) and
_card_label (the Sessions-card label precedence + the capture-UUID repair shim).
"""

from __future__ import annotations

import pytest

from recon.sessions.service import (
    SessionInvalid,
    _card_label,
    _resolve_scope_hosts,
    _target_host,
)


def test_explicit_scope_is_normalized_and_deduped():
    assert _resolve_scope_hosts(["ACME.IO", "cdn.acme.io.", "acme.io"], None) == [
        "acme.io",
        "cdn.acme.io",
    ]


def test_invalid_scope_entry_is_rejected():
    for bad in ["localhost", "com", "10.0.0.1", "github.io", "https://acme.io", "*"]:
        with pytest.raises(SessionInvalid, match="invalid scope host"):
            _resolve_scope_hosts([bad], None)


def test_wildcard_scope_entry_reduces_to_base_host():
    # "*.acme.io" is accepted and stored as the base host (covers subdomains, S1).
    assert _resolve_scope_hosts(["*.acme.io"], None) == ["acme.io"]
    assert _resolve_scope_hosts(["*.acme.io", "acme.io"], None) == ["acme.io"]  # deduped


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


# ---------------------------------------------------------------------------- #
# _card_label — the Sessions-card label precedence + the capture-UUID repair shim.
# The DB-backed derived_host (which asset URL feeds it) is covered by the integration
# suite (capture_router_test); the precedence itself is pure and pinned here.
# ---------------------------------------------------------------------------- #

_UUID = "0b3e6d2a-1c4f-4a9b-8e7d-2f5a6c9b1d3e"  # an extension crypto.randomUUID()


def test_card_label_user_rename_wins_over_everything():
    assert (
        _card_label(
            name="Prod audit",
            external_id=None,
            target="acme.io",
            derived_host=None,
            scope_hosts=["acme.io"],
        )
        == "Prod audit"
    )


def test_card_label_crawl_uses_target():
    assert (
        _card_label(
            name=None,
            external_id=None,
            target="acme.io",
            derived_host=None,
            scope_hosts=["acme.io"],
        )
        == "acme.io"
    )


def test_card_label_capture_uuid_name_falls_through_to_derived_host():
    # A capture session's auto-assigned name IS its ext-UUID (== external_id): the
    # shim treats it as unnamed so the captured host shows, never the raw UUID.
    assert (
        _card_label(
            name=_UUID,
            external_id=_UUID,
            target=None,
            derived_host="app.acme.io",
            scope_hosts=[],
        )
        == "app.acme.io"
    )


def test_card_label_renamed_capture_still_wins_over_derived_host():
    # A genuine rename (name != external_id) is NOT suppressed by the shim.
    assert (
        _card_label(
            name="My capture",
            external_id=_UUID,
            target=None,
            derived_host="app.acme.io",
            scope_hosts=[],
        )
        == "My capture"
    )


def test_card_label_shim_is_capture_only_app_session_named_none():
    # §4 finding-1 guard: an app session (external_id is None) a user literally renamed
    # "None" must NOT be swallowed by the shim (str(None) == "None").
    assert (
        _card_label(
            name="None",
            external_id=None,
            target=None,
            derived_host=None,
            scope_hosts=["acme.io"],
        )
        == "None"
    )


def test_card_label_upload_with_no_assets_falls_back_to_scope_host():
    # A single-blob upload is target-less with no run_asset rows -> derived_host None.
    assert (
        _card_label(
            name=None,
            external_id=None,
            target=None,
            derived_host=None,
            scope_hosts=["acme.io"],
        )
        == "acme.io"
    )


def test_card_label_nothing_resolves_is_em_dash():
    assert (
        _card_label(
            name=None,
            external_id=None,
            target=None,
            derived_host=None,
            scope_hosts=[],
        )
        == "—"
    )


# ---------------------------------------------------------------------------- #
# _target_host — clean the crawl target to a bare host for the card label (D27).
# Crash-guarded on purpose: run.target is nullable free-text (the upload path stores
# it unvalidated), and one raising row would 500 the whole /sessions list.
# ---------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (None, ""),  # nullable target must not raise (would 500 the list)
        ("", ""),
        ("   ", ""),  # whitespace-only -> split() is [] -> no IndexError
        ("acme.io", "acme.io"),
        ("www.nhl.com/stats", "www.nhl.com"),  # drop the path
        ("https://acme.io:8443/app.js", "acme.io"),  # drop scheme/port/path
        ("visa.com — verify classified failure", "visa.com"),  # drop the free-text memo
    ],
)
def test_target_host_cleans_or_empties(target: str | None, expected: str):
    assert _target_host(target) == expected


def test_card_label_crawl_target_is_normalized_to_host():
    # A path/free-text target shows only its host on the card, never the raw string (D27).
    assert (
        _card_label(
            name=None,
            external_id=None,
            target="www.nhl.com/stats",
            derived_host=None,
            scope_hosts=["www.nhl.com"],
        )
        == "www.nhl.com"
    )


def test_card_label_unparseable_target_falls_through_and_never_raises():
    # A blank/whitespace target must fall through to the next label source, not raise —
    # a single raising row would 500 the whole Sessions list.
    assert (
        _card_label(
            name=None,
            external_id=None,
            target="   ",
            derived_host="app.acme.io",
            scope_hosts=[],
        )
        == "app.acme.io"
    )
