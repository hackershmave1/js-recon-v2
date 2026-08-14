"""Fast-lane tests for auth-service helpers that need no DB. The login + seed paths
that hit Postgres are covered by the ``@pytest.mark.integration`` cases in
``api/auth_router_test.py`` (incl. the end-to-end case-insensitive login)."""

from __future__ import annotations

import pytest

from recon.auth.service import normalize_username


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("admin", "admin"),
        ("Admin", "admin"),
        ("ADMIN", "admin"),
        ("  admin  ", "admin"),
        ("\tOperator@Example.com\n", "operator@example.com"),
    ],
)
def test_normalize_username_trims_and_lowercases(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected
