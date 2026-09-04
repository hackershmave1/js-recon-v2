"""Unit tests for the deterministic finding priority (D49). Pure, no DB."""

from __future__ import annotations

from recon.findings.priority import derive_priority, priority_label, priority_score


def test_secret_outranks_a_suspected_endpoint() -> None:
    assert priority_score("secret") > priority_score("endpoint_suspected")
    assert priority_score("endpoint") > priority_score("page_route")


def test_risk_tag_bump_is_the_max_not_the_sum() -> None:
    # A multi-tagged param bumps by its single highest tag, so it can't run past a real secret.
    both = priority_score("endpoint", ["auth", "admin"])
    assert both == priority_score("endpoint", ["admin"])  # admin(25) dominates auth(15)
    assert both < priority_score("secret")


def test_admin_and_idor_beat_auth_and_flag() -> None:
    base = priority_score("endpoint")
    assert priority_score("endpoint", ["admin"]) - base == 25
    assert priority_score("endpoint", ["idor"]) - base == 20
    assert priority_score("endpoint", ["auth"]) - base == 15
    assert priority_score("endpoint", ["flag"]) - base == 5


def test_score_is_capped_at_100() -> None:
    assert priority_score("secret", ["admin"]) == 100  # 90 + 25 -> clamped


def test_unknown_type_falls_back_to_default() -> None:
    assert priority_score("something_new") == 20


def test_label_thresholds() -> None:
    assert priority_label(100) == "critical"
    assert priority_label(80) == "critical"
    assert priority_label(79) == "high"
    assert priority_label(50) == "high"
    assert priority_label(30) == "medium"
    assert priority_label(29) == "low"
    assert priority_label(0) == "low"


def test_derive_reads_risk_tags_from_attributes() -> None:
    score, label = derive_priority("endpoint", {"risk_tags": ["admin"]})
    assert score == priority_score("endpoint", ["admin"])
    assert label == priority_label(score)


def test_derive_tolerates_missing_or_malformed_attributes() -> None:
    for attrs in (None, {}, {"risk_tags": None}, {"risk_tags": "admin"}):
        score, label = derive_priority("endpoint", attrs)  # type: ignore[arg-type]
        assert score == priority_score("endpoint")  # no tags applied, never raises
        assert label == priority_label(score)
