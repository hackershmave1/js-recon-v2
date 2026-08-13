"""Pure unit tests for param risk-tagging. The false-positive guards (idor/auth) are the
load-bearing invariant, so they get the most cases."""

from __future__ import annotations

import pytest

from recon.findings.risk_tags import classify_param


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # auth
        ("token", ("auth",)),
        ("authorization", ("auth",)),
        ("apiKey", ("auth",)),
        ("api_key", ("auth",)),
        ("x-api-key", ("auth",)),
        ("jwt", ("auth",)),
        ("password", ("auth",)),
        ("accessToken", ("auth",)),
        ("sessionId", ("auth", "idor")),  # a session id is both a credential and an identifier
        # admin
        ("admin", ("admin",)),
        ("impersonate", ("admin",)),
        ("superuser", ("admin",)),
        # idor (M1: whole-token id/uuid/guid only)
        ("userId", ("idor",)),
        ("account_id", ("idor",)),
        ("objectId", ("idor",)),
        ("uuid", ("idor",)),
        ("guid", ("idor",)),
        # flag
        ("featureFlag", ("flag",)),
        ("isEnabled", ("flag",)),  # via the "enabled" token, not a bare is-prefix
        ("beta", ("flag",)),
        ("toggle", ("flag",)),
        # multi-tag
        ("admin_token", ("admin", "auth")),
        # untagged (the common case)
        ("email", ()),
        ("name", ()),
        ("page", ()),
        ("limit", ()),
        ("", ()),
    ],
)
def test_classify_param(name: str, expected: tuple[str, ...]) -> None:
    assert classify_param(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "valid",
        "grid",
        "android",
        "solid",
        "rapid",
        "hybrid",
        "liquid",
        "candidate",
        "width",
        "video",
    ],
)
def test_idor_never_matches_a_word_that_merely_ends_in_id(name: str) -> None:
    # M1: the classic substring trap. These must NOT be idor.
    assert "idor" not in classify_param(name)


@pytest.mark.parametrize("name", ["nextToken", "pageToken", "continuationToken", "sessionStorage"])
def test_pagination_and_storage_are_not_auth(name: str) -> None:
    # S1: a pagination cursor / browser storage handle is not a credential.
    assert "auth" not in classify_param(name)


@pytest.mark.parametrize("name", ["rootMargin", "rootElement", "gridColumn"])
def test_no_admin_false_positive_from_root(name: str) -> None:
    # S1: "root" was dropped from the admin set (rootId/rootMargin are not privilege controls).
    assert "admin" not in classify_param(name)


def test_result_is_sorted_and_deduped() -> None:
    tags = classify_param("adminApiKeyToken")  # admin + several auth signals
    assert tags == tuple(sorted(set(tags)))
    assert "admin" in tags and "auth" in tags
