"""Colocated unit tests for the pure wrapper-rule value + callee validator."""

from __future__ import annotations

import pytest

from recon.findings.wrappers import (
    InvalidWrapperCallee,
    WrapperRule,
    validate_callee,
    wrapper_callees,
)


def test_wrapper_rule_is_frozen_value():
    rule = WrapperRule(callee="api")
    assert rule.callee == "api"
    with pytest.raises(Exception):
        rule.callee = "other"  # type: ignore[misc]  # frozen dataclass


def test_wrapper_callees_builds_a_set():
    assert wrapper_callees([WrapperRule("api"), WrapperRule("apiClient")]) == frozenset(
        {"api", "apiClient"}
    )


@pytest.mark.parametrize("callee", ["api", "apiClient", "_http", "$api", "a1"])
def test_validate_callee_accepts_bare_identifiers(callee):
    validate_callee(callee)  # does not raise


@pytest.mark.parametrize("callee", ["this.httpClient", "this.http", "a.b", "a.b.c", "svc.$api"])
def test_validate_callee_accepts_dotted_receivers(callee):
    validate_callee(callee)  # does not raise — dotted receiver (spec §4 fast-follow)


@pytest.mark.parametrize("callee", ["", "1abc", "a b", "api()", ".x", "a.", "a..b", "a.1b"])
def test_validate_callee_rejects_non_identifiers(callee):
    with pytest.raises(InvalidWrapperCallee):
        validate_callee(callee)
