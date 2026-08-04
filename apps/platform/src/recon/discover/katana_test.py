# src/recon/discover/katana_test.py
from recon.discover import katana


def test_build_argv_standard_by_default():
    argv = katana.build_argv(
        katana_bin="katana", domain="acme.io", scope_hosts=["acme.io"],
        depth=3, crawl_duration_seconds=120.0,
    )
    assert argv[0] == "katana"
    assert argv[argv.index("-u") + 1] == "https://acme.io"
    assert "-jsonl" in argv
    assert argv[argv.index("-field-scope") + 1] == "rdn"
    assert "-crawl-scope" in argv
    assert "-jc" not in argv   # discovery-only: Vespasian parses, not katana
    assert "-em" not in argv   # -em js filtered everything; parse_assets filters .js
    assert "-headless" not in argv  # standard by default


def test_build_argv_headless_is_opt_in():
    argv = katana.build_argv(
        katana_bin="katana", domain="acme.io", scope_hosts=["acme.io"],
        depth=3, crawl_duration_seconds=120.0, headless=True,
    )
    assert "-headless" in argv
    assert "-no-sandbox" in argv
    assert argv[argv.index("-headless-options") + 1] == "--disable-dev-shm-usage"
    assert "-em" not in argv
    assert "-system-chrome" not in argv
    assert "-system-chrome-path" not in argv  # omitted when no path is supplied


def test_build_argv_headless_uses_system_chrome_path_when_supplied():
    # Points katana at the image's baked-in chromium so go-rod does not download
    # its own browser at runtime (verified against katana v1.6.1 + chromium 150).
    argv = katana.build_argv(
        katana_bin="katana", domain="acme.io", scope_hosts=["acme.io"],
        depth=3, crawl_duration_seconds=120.0, headless=True,
        system_chrome_path="/usr/bin/chromium",
    )
    assert "-headless" in argv
    assert argv[argv.index("-system-chrome-path") + 1] == "/usr/bin/chromium"


def test_build_argv_system_chrome_path_ignored_without_headless():
    # No headless -> standard crawl, so the chrome path is irrelevant and dropped.
    argv = katana.build_argv(
        katana_bin="katana", domain="acme.io", scope_hosts=["acme.io"],
        depth=3, crawl_duration_seconds=120.0, system_chrome_path="/usr/bin/chromium",
    )
    assert "-headless" not in argv
    assert "-system-chrome-path" not in argv


def test_parse_assets_keeps_ordered_unique_js_urls():
    stdout = b"\n".join([
        b'{"request":{"endpoint":"https://acme.io/static/app.js"}}',
        b'not json - skipped',
        b'{"request":{"endpoint":"https://acme.io/vendor.js"}}',
        b'{"request":{"endpoint":"https://acme.io/static/app.js"}}',  # dup
        b'{"request":{"endpoint":"https://acme.io/index.html"}}',      # not .js
        b'{"endpoint":"https://acme.io/legacy.js"}',                    # top-level field
    ])
    assert katana.parse_assets(stdout) == [
        "https://acme.io/static/app.js",
        "https://acme.io/vendor.js",
        "https://acme.io/legacy.js",
    ]
