from recon.findings.techdetect import compile as tc
from recon.findings.techdetect import dataset


def test_load_raw_returns_technologies_categories_and_commit():
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
