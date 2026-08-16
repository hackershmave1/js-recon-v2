from recon.findings.techdetect import version


def test_parse_plain_pattern_has_no_tags():
    regex, tags = version.parse_field_value("Express")
    assert regex == "Express"
    assert tags.version is None
    assert tags.confidence == 100  # enthec default when unspecified


def test_parse_splits_version_and_confidence_tags():
    regex, tags = version.parse_field_value(r"nginx(?:/([\d.]+))?\;version:\1\;confidence:50")
    assert regex == r"nginx(?:/([\d.]+))?"
    assert tags.version == r"\1"
    assert tags.confidence == 50


def test_resolve_substitutes_capture_group():
    assert version.resolve_version(r"\1", ("1.25.3",)) == "1.25.3"


def test_resolve_empty_group_yields_none_not_blank():
    assert version.resolve_version(r"\1", (None,)) is None


def test_resolve_ternary_present_and_absent():
    # \1?a:b -> a when group 1 matched (truthy), b when it didn't
    assert version.resolve_version(r"\1?4:3", ("something",)) == "4"
    assert version.resolve_version(r"\1?4:3", (None,)) == "3"


def test_resolve_none_template_is_none():
    assert version.resolve_version(None, ("x",)) is None


def test_resolve_ternary_empty_string_group_is_falsy():
    # A matched-but-empty capture group ("") must still pick the absent branch.
    # Guards `if _group(...)` truthiness against a future `is not None` refactor,
    # which would wrongly treat "" as present.
    assert version.resolve_version(r"\1?4:3", ("",)) == "3"


def test_resolve_multiple_group_references():
    assert version.resolve_version(r"\1.\2", ("3", "5")) == "3.5"


def test_resolve_out_of_range_group_index_is_safe():
    # \2 with only one capture group present: no IndexError, substitutes "".
    assert version.resolve_version(r"\2", ("3",)) is None


def test_resolve_group_reference_against_empty_tuple_is_safe():
    # No capture groups at all: no IndexError, substitutes "".
    assert version.resolve_version(r"\1", ()) is None


def test_resolve_substituted_group_value_is_not_re_substituted():
    # Group 1's value looks like a backref (r"\2"). A naive second pass over the
    # already-substituted text would wrongly re-substitute it; it must stay literal.
    assert version.resolve_version(r"\1-\2", (r"\2", "9")) == r"\2-9"


def test_parse_malformed_confidence_falls_back_to_default():
    regex, tags = version.parse_field_value("foo\\;confidence:abc")
    assert regex == "foo"
    assert tags.version is None
    assert tags.confidence == 100


def test_parse_unknown_tag_is_ignored():
    regex, tags = version.parse_field_value("foo\\;whatever:x")
    assert regex == "foo"
    assert tags.version is None
    assert tags.confidence == 100
