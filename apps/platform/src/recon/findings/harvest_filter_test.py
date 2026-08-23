"""Off-sink harvest garbage-host filter (`_harvest_denied`) — DEBT D34.

The off-sink route harvest turns any absolute-URL string literal in a bundle into a
``page_route``/``endpoint_generic`` by SHAPE ALONE (no sink/nav flow required). Before D34 the
only guard was a 5-entry raw SUBSTRING denylist that was both too small and wrong in both
directions: ``schema.org`` is not a substring of ``schemas.openxmlformats.org`` (missed real
garbage) and ``example.com`` IS a substring of ``notexample.com`` (would drop a real host). On a
real 4.4 MB SheetJS+React bundle ~95% of the harvested "hosts" were OOXML/ODF/library boilerplate.

These tests pin the replacement — an EXACT host-or-dot-suffix denylist + a scheme allow-list + an
``http://`` XML-namespace-shape rule — verifying vendored boilerplate is dropped while real
API/auth/nav hosts survive. The confirmed-``endpoint`` lane never calls this (a sink URL is
claimed, not harvested), so filtering here can never hide a real backend endpoint.
"""

from recon.findings.extract import _harvest_denied, extract

# --- unit: the predicate ----------------------------------------------------------------


def test_namespace_registrars_denied_by_exact_suffix() -> None:
    for url in (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "https://sheetjs.openxmlformats.org/",  # dot-suffix of a listed registrable domain
        "http://schemas.microsoft.com/office/2006/relationships",
        "http://openoffice.org/2004/writer",
        "http://purl.oclc.org/ooxml/spreadsheetml/main",  # no year -> caught by the denylist
        "http://www.w3.org/2000/svg",
        "https://schema.org/Person",
    ):
        assert _harvest_denied(url) is True, url


def test_library_doc_hosts_denied() -> None:
    for url in (
        "https://reactjs.org/docs/error-decoder.html",
        "https://redux.js.org/api/store",
        "https://popper.js.org/docs/v2/",
        "https://www.ag-grid.com/javascript-data-grid/",
    ):
        assert _harvest_denied(url) is True, url


def test_public_suffix_and_third_party_hosts_are_kept() -> None:
    # A bare public suffix (`github.io`) or a third-party host (`fb.me`) must NOT be denylisted:
    # doing so would shadow a target's OWN off-sink routes (§4 review HIGH-1, DEBT D34).
    assert _harvest_denied("https://myapp.github.io/dashboard") is False
    assert _harvest_denied("wss://rt.myapp.github.io/socket") is False
    assert _harvest_denied("https://acme.fb.me/promo") is False


def test_substring_bug_regression_real_host_kept() -> None:
    # OVER-match direction: the OLD substring test dropped `notexample.com` (contains
    # `example.com`) and `mypurl.org` (contains `purl.org`); exact-suffix keeps both real hosts.
    assert _harvest_denied("https://notexample.com/dashboard") is False
    assert _harvest_denied("https://mypurl.org/api") is False
    # MISSED-garbage direction: `purl.org` is NOT a substring of `purl.oclc.org`, so the old
    # test let it through; it is now denied (explicit denylist entry).
    assert _harvest_denied("http://purl.oclc.org/ooxml/main") is True
    # ...and the RFC example domain itself is still denied.
    assert _harvest_denied("https://example.com/x") is True


def test_scheme_allow_list_drops_non_web_schemes() -> None:
    for url in ("file:///C:/SheetJS/demo", "file://EXPR/x", "blob:https://acme.io/uuid"):
        assert _harvest_denied(url) is True, url
    # a normal https URL is not rejected on scheme grounds.
    assert _harvest_denied("https://app.acme.io/home") is False


def test_namespace_shape_generalizes_only_for_http() -> None:
    # an UNLISTED registrar is still caught by shape: http + a NON-TERMINAL dated segment + not
    # API-ish (`/2021/relationships`).
    assert _harvest_denied("http://schemas.newvendor.org/2021/relationships") is True
    # ...but a TERMINAL numeric segment is an id, not a year, and must be kept (§4 review MED-1).
    assert _harvest_denied("http://shop.acme.com/products/2020") is False
    # ...a real HTTPS content route with a dated path is NEVER dropped (shape rule is http-only).
    assert _harvest_denied("https://blog.acme.com/2024/black-friday") is False
    # ...nor an api-ish http URL with a year segment (the shape rule is gated on `not api-ish`).
    assert _harvest_denied("http://api.acme.io/v1/2020/report") is False


def test_real_api_auth_and_nav_hosts_kept() -> None:
    for url in (
        "https://login.microsoftonline.com/common/oauth2/authorize",
        "https://api.acme.io/v1/users",
        "https://cdn.acme.io/live/stream",
        "https://app.acme.com/player/42",
    ):
        assert _harvest_denied(url) is False, url


# --- integration: extract() end-to-end --------------------------------------------------


def test_extract_drops_namespace_literal_keeps_real_route() -> None:
    # a vendored xmlns constant is filtered; a real off-sink absolute route is still harvested.
    src = (
        'var ns = "http://schemas.openxmlformats.org/package/2006/relationships";'
        'var u = "https://cdn.acme.io/".concat("live/").concat(id);'
    )
    r = extract(src)
    urls = [x.url for x in (*r.routes, *r.generic)]
    assert not any("openxmlformats" in u for u in urls)
    assert any(u.url.startswith("https://cdn.acme.io/") for u in r.routes)


def test_extract_keeps_api_host_in_generic_lane() -> None:
    # an /oauth auth endpoint literal is api-ish -> the generic (suspected-API) lane, not noise.
    r = extract('var a = "https://login.microsoftonline.com/common/oauth2/authorize";')
    assert any("login.microsoftonline.com" in g.url for g in r.generic)
    assert r.routes == []


def test_extract_file_scheme_literal_is_not_harvested() -> None:
    assert extract('var p = "file:///C:/build/vendor/sheetjs.js";').routes == []


def test_denied_composite_does_not_leak_truncated_subliteral() -> None:
    # MED-2 (§4 review): a denied composite builder must not let its inner string child harvest
    # as a truncated route. The composite is denied by the http year-shape rule; its leading
    # literal `http://cdn.vendorlib.io/` must not survive as a separate route.
    r = extract('var u = "http://cdn.vendorlib.io/".concat("2006/spec");')
    assert "http://cdn.vendorlib.io/" not in [x.url for x in r.routes]
