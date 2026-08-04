import pytest

from recon.findings.base_url import (
    BaseUrlRule,
    InvalidBaseUrl,
    resolve_operation,
    validate_base_url,
)


def _prefix(prefix, base):
    return BaseUrlRule(kind="prefix", base_url=base, path_prefix=prefix)


def _selection(hashes, base):
    return BaseUrlRule(kind="selection", base_url=base, finding_hashes=tuple(hashes))


def test_prefix_rule_prepends_whole_path():
    r = resolve_operation("GET", "/address/search", ("h1",), False, [_prefix("/address", "/location")])
    assert r.path == "/location/address/search"
    assert r.host is None and r.changed is True


def test_selection_rule_matches_by_hash():
    r = resolve_operation("GET", "/address/search", ("h1",), False, [_selection(["h1"], "/location")])
    assert r.path == "/location/address/search" and r.changed is True


def test_selection_beats_prefix():
    rules = [_prefix("/address", "/wrong"), _selection(["h1"], "/right")]
    r = resolve_operation("GET", "/address/x", ("h1",), False, rules)
    assert r.path == "/right/address/x"


def test_longest_prefix_wins():
    rules = [_prefix("/a", "/short"), _prefix("/a/b", "/long")]
    r = resolve_operation("GET", "/a/b/c", ("h1",), False, rules)
    assert r.path == "/long/a/b/c"


def test_segment_boundary_match_only():
    # '/address' must NOT match '/address-svc/...'
    r = resolve_operation("GET", "/address-svc/x", ("h1",), False, [_prefix("/address", "/location")])
    assert r.path == "/address-svc/x" and r.changed is False


def test_absolute_op_is_not_rebased():
    # has_host True => the op already carries a resolved host; never re-base it (gate B1).
    r = resolve_operation("GET", "/location/address/search", ("h1",), True, [_prefix("/location", "/x")])
    assert r.path == "/location/address/search" and r.changed is False


def test_host_bearing_base_sets_host_and_scheme():
    r = resolve_operation("GET", "/x", ("h1",), False, [_prefix("/x", "https://api.example.com/v3")])
    assert r.path == "/v3/x"
    assert r.host == "api.example.com" and r.scheme == "https"


def test_idempotent_when_already_under_base():
    rule = _prefix("/address", "/location")
    once = resolve_operation("GET", "/address/search", ("h1",), False, [rule])
    twice = resolve_operation("GET", once.path, ("h1",), False, [rule])
    assert twice.path == once.path  # '/location/address/search' no longer matches '/address'


def test_selection_rule_idempotent():
    # A selection rule matches by hash regardless of path, so idempotence rests on
    # the segment-prefix guard, not on the path ceasing to match (as it does for a
    # prefix rule). Re-applying must not double-prepend the base.
    rule = _selection(["h1"], "/location")
    once = resolve_operation("GET", "/address/search", ("h1",), False, [rule])
    twice = resolve_operation("GET", once.path, ("h1",), False, [rule])
    assert once.path == "/location/address/search"
    assert twice.path == once.path and twice.changed is False


def test_overlapping_selection_first_in_list_wins():
    # At most one rule applies; among OVERLAPPING selection rules _match returns the
    # first in the given order (asymmetric — the caller must order them, and the
    # query layer orders by most-recent updated_at so the newest rule wins).
    hi, lo = _selection(["h1"], "/new"), _selection(["h1"], "/old")
    assert resolve_operation("GET", "/x", ("h1",), False, [hi, lo]).path == "/new/x"
    assert resolve_operation("GET", "/x", ("h1",), False, [lo, hi]).path == "/old/x"


def test_no_matching_rule_is_unchanged():
    r = resolve_operation("GET", "/other", ("h1",), False, [_prefix("/address", "/location")])
    assert r.path == "/other" and r.changed is False


def test_validate_rejects_bad_bases():
    for bad in ["", "location", "ftp://h/x", "https:///x", "/x?y=1", "https://u:p@h/x"]:
        with pytest.raises(InvalidBaseUrl):
            validate_base_url(bad)


def test_validate_accepts_good_bases():
    for good in ["/location", "/a/b", "https://api.example.com", "http://h:8443/v3"]:
        validate_base_url(good)  # must not raise
