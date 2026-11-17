"""Issue #235: an unresolved sink whose URL comes from the page names its source.

Two guarantees, both pinned here:
- each source kind yields its display token (`{attr:data-url}`, `{stimulus:url}`, ...);
- the finding's identity is byte-identical to before the token existed (value AND hash), so
  existing triage carries over ("show, don't refile").
"""

from __future__ import annotations

import time

import pytest

from recon.domain import FindingType
from recon.findings import normalize
from recon.findings._jsast import _PARSER
from recon.findings._url_source import url_source_token
from recon.findings.extract import extract
from recon.findings.store import Occurrence

# snippet -> (finding value as recorded today, url_source token). The values were captured
# from main BEFORE this change; they must never move.
CASES = {
    'fetch($(el).attr("data-url"))': ("GET EXPR", "{attr:data-url}"),
    'fetch(el.getAttribute("data-url"))': ("GET EXPR", "{attr:data-url}"),
    '$.ajax({url: $(f).prop("action"), type: "POST"})': ("POST EXPR", "{attr:action}"),
    '$.get($(el).data("js-ajax-href"))': ("GET EXPR", "{data:js-ajax-href}"),
    "fetch(el.dataset.validationUrl)": ("GET :validationUrl", "{data:validation-url}"),
    'fetch(el.dataset["validationUrl"])': ("GET EXPR", "{data:validation-url}"),
    "fetch(this.urlValue)": ("GET :urlValue", "{stimulus:url}"),
    'fetch(this.minifyUrlValue + "?q=" + q)': (
        "GET :minifyUrlValue?q=EXPR",
        "{stimulus:minify-url}",
    ),
    'fetch(this.data.get("url"))': ("GET EXPR", "{stimulus:url}"),
    'fetch(form.action, {method: "POST"})': ("POST :action", "{form:action}"),
    "fetch(e.target.action)": ("GET :action", "{form:action}"),
    "fetch(link.href)": ("GET :href", "{attr:href}"),
    "axios.post(this.urlValue, body)": ("POST :urlValue", "{stimulus:url}"),
    'x.open("GET", $(el).data("url"))': ("GET EXPR", "{data:url}"),
    "new WebSocket(this.element.dataset.socketUrl)": ("WS :socketUrl", "{data:socket-url}"),
    'fetch(this.urlValue || "/fallback")': ("GET EXPR", "{stimulus:url}"),
}

# The sha256(type, value) of a few of the above, computed on main before this change.
PINNED_HASHES = {
    "GET EXPR": "8eca6018a5e649f28be133878ee3cc46b54fc59e5b448b97f79155edff8d3dd7",
    "POST EXPR": "f2c1c9469c5f8a083d5fbe7818dd360861c26157795027e6f2ff4e0fff48c92f",
    "GET :urlValue": "989f48e2ec5847d45b12d7dad9aba36bb2f4fef1382a0d94188d24448fb277fd",
    "GET :minifyUrlValue?q=EXPR": "db2eef85826a476afbbf9deb9c4d3ea365d59bd5930d3005089979bf226ef185",
    "POST :action": "eb2026f795cbb6b3672f3e5656ed05f5be52e9d06ec9bdc266e726c378ce3faf",
    "WS :socketUrl": "9e0cff3b6dd132b5163a45fb38949e3b663bcea94470d6dc1b45cfbee88b452d",
}


def _only_unresolved(source: str):
    extraction = extract(source)
    assert extraction.endpoints == []
    rows = [*extraction.unresolved, *extraction.generic]
    assert len(rows) == 1, rows
    return rows[0]


@pytest.mark.parametrize(("source", "expected"), CASES.items(), ids=list(CASES))
def test_each_page_source_kind_gets_its_token(source, expected):
    value, token = expected
    row = _only_unresolved(source)
    assert f"{row.method} {row.url}".strip() == value  # identity value unchanged
    assert row.url_source == token


@pytest.mark.parametrize(("value", "expected"), PINNED_HASHES.items())
def test_finding_hash_is_byte_identical_to_before(value, expected):
    assert normalize.finding_hash(FindingType.ENDPOINT_UNRESOLVED, value) == expected


def test_suspected_endpoint_value_unchanged_and_token_kept():
    row = _only_unresolved('fetch($(el).attr("data-url") + "/items/" + id)')
    assert row.url == "EXPR/items/:id"
    suspected = normalize.normalize_suspected_endpoint(row.method, row.url)
    assert suspected is not None and suspected.value == "GET /EXPR/items/{id}"
    assert normalize.finding_hash(FindingType.ENDPOINT_SUSPECTED, suspected.value) == (
        "8245a78c9e51d487566bd009698b3206fdaf5712e08e35622889675ab8066aa3"
    )
    assert row.url_source == "{attr:data-url}"


def test_two_sources_both_shown_in_order():
    row = _only_unresolved('fetch($(a).attr("data-base") + el.dataset.path)')
    assert row.url_source == "{attr:data-base} {data:path}"


@pytest.mark.parametrize(
    "source",
    [
        "fetch(u)",  # a minified variable: nothing in the page to point at
        "fetch(cfg.url)",
        "fetch($(el).attr(name))",  # computed attribute name: never guessed
        'fetch($(el).attr("{evil}"))',  # would break the token: refused
        "fetch(window.location.href)",  # the page itself, not an attribute
        "fetch(getUrl())",
    ],
)
def test_no_token_when_the_source_is_not_the_page(source):
    extraction = extract(source)
    for row in [*extraction.unresolved, *extraction.generic, *extraction.endpoints]:
        assert row.url_source is None


def test_resolved_endpoint_carries_no_token():
    extraction = extract('fetch("/api/users")')
    assert [e.url_source for e in extraction.endpoints] == [None]


def test_token_is_not_part_of_occurrence_identity():
    # A retry / re-extract that adds the token must dedupe onto the same occurrence row.
    base = Occurrence(raw_url="EXPR", source_path="a.js", offset_start=1, offset_end=9)
    with_token = Occurrence(
        raw_url="EXPR", source_path="a.js", offset_start=1, offset_end=9, url_source="{form:action}"
    )
    assert normalize.occurrence_hash(**base._identity()) == normalize.occurrence_hash(
        **with_token._identity()
    )


def test_template_substitution_is_searched():
    template = _PARSER.parse(b"(`${this.urlValue}/x`)").root_node.named_children[0]
    assert url_source_token(template.named_children[0]) == "{stimulus:url}"


def test_url_source_token_none_for_missing_node():
    assert url_source_token(None) is None


@pytest.mark.dos_timing  # wall-clock guard: host-lane-only (see pyproject / DEBT D21)
def test_token_walk_is_bounded_on_a_huge_concat_chain():
    # 20k `attr("data-x")` operands: the walk stops at its visit budget, not after all of them.
    chain = " + ".join(f'$(e).attr("data-k{i}")' for i in range(20000))
    node = _PARSER.parse(f"({chain})".encode()).root_node.named_children[0].named_children[0]
    start = time.perf_counter()
    for _ in range(200):
        token = url_source_token(node)
    assert time.perf_counter() - start < 1.0
    assert token is not None and token.count("{attr:") == 3
