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
