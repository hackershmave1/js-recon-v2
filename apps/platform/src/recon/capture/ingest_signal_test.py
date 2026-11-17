"""Unit tests for the extension-ingest fingerprint-signal builder (DEBT D77). Pure."""

from __future__ import annotations

from recon.capture.ingest_signal import build_batch_signal


def test_builds_host_keyed_signal_from_uploaded_headers_and_urls() -> None:
    signal = build_batch_signal(
        [
            (
                "https://app.acme.io/static/app.js",
                {"Server": "nginx/1.25.3", "X-Powered-By": "Express", "ETag": "abc"},
            ),
            ("https://app.acme.io/static/vendor.js", {"server": "nginx/1.25.3"}),
        ]
    )

    assert signal == {
        "app.acme.io": {
            # Keys lowercased; non-allowlisted headers (etag) dropped — the same
            # allowlist choke point as the crawl/capture producers (T1).
            "headers": {"server": "nginx/1.25.3", "x-powered-by": "Express"},
            "scripts": [
                "https://app.acme.io/static/app.js",
                "https://app.acme.io/static/vendor.js",
            ],
            "meta": [],
            "cookies": [],
        }
    }


def test_keeps_cookie_names_never_values() -> None:
    signal = build_batch_signal(
        [("https://acme.io/a.js", {"set-cookie": "laravel_session=SECRET; path=/\nXSRF-TOKEN=x"})]
    )

    assert signal["acme.io"]["cookies"] == ["XSRF-TOKEN", "laravel_session"]
    assert "SECRET" not in repr(signal)
    assert "set-cookie" not in signal["acme.io"]["headers"]


def test_a_file_without_headers_still_contributes_its_script_url() -> None:
    # An older extension (or a file it had no response for) sends no headers; the
    # script URL alone still drives the scriptSrc surface and the JS-content match.
    signal = build_batch_signal([("https://acme.io/jquery.min.js", None)])

    assert signal["acme.io"]["scripts"] == ["https://acme.io/jquery.min.js"]
    assert signal["acme.io"]["headers"] == {}


def test_malformed_headers_and_urls_are_skipped_never_raised() -> None:
    signal = build_batch_signal(
        [
            ("https://[::1/broken.js", {"server": "nginx"}),  # invalid IPv6 literal
            ("blob:inline", {"server": "nginx"}),  # no host
            ("https://acme.io/a.js", ["not", "a", "dict"]),
            ("https://acme.io/b.js", {"server": 7, 3: "x"}),  # non-str values/keys
        ]
    )

    assert set(signal) == {"acme.io"}
    assert signal["acme.io"]["headers"] == {"server": "7"}
    assert signal["acme.io"]["scripts"] == ["https://acme.io/a.js", "https://acme.io/b.js"]


def test_empty_batch_is_an_empty_signal() -> None:
    assert build_batch_signal([]) == {}


def test_a_file_from_an_off_scope_host_contributes_nothing() -> None:
    # T1 parity with the server-side capture: a third party the page loaded (ads, analytics,
    # a CDN outside the declared scope) never has its headers or URL persisted.
    signal = build_batch_signal(
        [
            ("https://app.acme.io/a.js", {"server": "nginx"}),
            ("https://www.googletagmanager.com/gtm.js", {"server": "Google Tag Manager"}),
        ],
        scope_hosts=["acme.io"],
    )

    assert set(signal) == {"app.acme.io"}


def test_no_declared_scope_keeps_every_host() -> None:
    signal = build_batch_signal(
        [("https://a.io/x.js", None), ("https://b.io/y.js", None)], scope_hosts=None
    )

    assert set(signal) == {"a.io", "b.io"}
