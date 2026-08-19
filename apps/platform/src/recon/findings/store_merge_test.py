"""Pure unit tests for the v2 attribute-merge (store._merge_attributes).

When v2 identity collapses two findings that used to differ only by source path
onto one row, their finding-level ``attributes`` must be unioned, not first-writer-
wins — otherwise a later sighting's observed auth header (attack surface) is
silently lost (REQ-C2). No DB needed: the helper is pure.
"""

from __future__ import annotations

from recon.findings.store import _merge_attributes


def test_auth_headers_are_unioned_never_dropped():
    existing = {"auth": [{"name": "Authorization", "scheme": "bearer"}], "kind": "fetch"}
    incoming = {"auth": [{"name": "X-Api-Key", "scheme": None}], "kind": "fetch"}
    merged = _merge_attributes(existing, incoming)
    names = {h["name"] for h in merged["auth"]}
    assert names == {"Authorization", "X-Api-Key"}  # neither header lost
    assert merged["kind"] == "fetch"  # agree -> kept


def test_duplicate_auth_header_is_not_double_counted():
    header = {"name": "Authorization", "scheme": "bearer"}
    merged = _merge_attributes({"auth": [header]}, {"auth": [dict(header)]})
    assert merged["auth"] == [header]  # exact-dup union is a no-op


def test_kind_disagreement_degrades_to_none():
    # fetch (JSON) merged with jquery (form) — the reconstructor must NOT then assert
    # a JSON Content-Type, so an ambiguous kind becomes None.
    merged = _merge_attributes({"kind": "fetch"}, {"kind": "jquery"})
    assert merged["kind"] is None


def test_risk_tags_are_set_unioned():
    merged = _merge_attributes({"risk_tags": ["a", "b"]}, {"risk_tags": ["b", "c"]})
    assert merged["risk_tags"] == ["a", "b", "c"]


def test_scalar_wrapper_keeps_first_non_null():
    assert _merge_attributes({"wrapper": "w1"}, {"wrapper": "w2"})["wrapper"] == "w1"
    assert _merge_attributes({}, {"wrapper": "w2"})["wrapper"] == "w2"


def test_merge_is_order_independent_and_idempotent():
    a = {"auth": [{"name": "A", "scheme": None}], "kind": "fetch", "risk_tags": ["x"]}
    b = {"auth": [{"name": "B", "scheme": "bearer"}], "kind": "axios", "risk_tags": ["y"]}
    ab = _merge_attributes(a, b)
    ba = _merge_attributes(b, a)
    assert {h["name"] for h in ab["auth"]} == {h["name"] for h in ba["auth"]} == {"A", "B"}
    assert ab["kind"] is None and ba["kind"] is None  # disagree -> None either way
    assert ab["risk_tags"] == ba["risk_tags"] == ["x", "y"]
    # re-merging an identical payload changes nothing (A3 retry safety)
    assert _merge_attributes(ab, b) == ab
