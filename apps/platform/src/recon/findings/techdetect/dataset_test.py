import json

import pytest

from recon.findings.techdetect import compile as tc
from recon.findings.techdetect import dataset


def test_load_raw_returns_technologies_categories_and_commit():
    """Also stands in as the "real vendored dataset loads non-empty" fail-closed
    check (T7): if the shipped technologies.json were ever empty, `dataset.load_raw`
    would raise via `_parse_raw`'s empty-guard and this test would fail on import."""
    techs, categories, commit = dataset.load_raw()
    assert "Nginx" in techs
    assert techs["Nginx"]["cats"]  # non-empty category id list
    assert categories["22"] == "Web servers"
    assert isinstance(commit, str) and commit


def test_category_names_resolves_ids_to_names():
    _techs, categories, _commit = dataset.load_raw()
    names = dataset.category_names([12, 59], categories)
    assert names == ["JavaScript frameworks", "JavaScript libraries"]


def test_load_is_cached_same_object():
    assert dataset.load_raw() is dataset.load_raw()


def test_compile_all_loads_all_and_reports_a_bounded_skip_count():
    techs, _categories, _commit = dataset.load_raw()
    compiled, skipped = tc.compile_all(techs)
    assert len(compiled) > 0
    # The curated subset is RE2-safe; a future full re-pin (refresh.py) keeps rejects
    # well under this bound — the load is never all-or-nothing (T4).
    assert skipped <= 40


# --- _parse_raw: the fail-closed contract (T7), isolated from importlib.resources ---
# so each path (missing text / corrupt JSON / syntactically-valid-but-empty) is a
# real, direct assertion rather than brittle monkeypatching of `resources.files`.


def test_parse_raw_rejects_empty_text_as_malformed_json():
    """No content at all is the simplest "corrupt/malformed JSON" case: json.loads
    itself raises JSONDecodeError before the empty-mapping guard ever runs."""
    with pytest.raises(json.JSONDecodeError):
        dataset._parse_raw("")


def test_parse_raw_rejects_syntactically_valid_but_empty_mapping():
    """The trap this fixes: `{}` parses fine but must still fail closed (T7) —
    a dataset with zero technologies is as unusable as a missing one."""
    with pytest.raises(ValueError, match="empty"):
        dataset._parse_raw("{}")


def test_parse_raw_returns_populated_structure_for_minimal_valid_json():
    text = '{"Nginx": {"cats": [22], "headers": {"Server": "nginx"}}}'
    assert dataset._parse_raw(text) == {"Nginx": {"cats": [22], "headers": {"Server": "nginx"}}}
