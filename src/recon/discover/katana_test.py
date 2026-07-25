# src/recon/discover/katana_test.py
from recon.discover import katana


def test_build_argv_is_headless_scoped_jsonl():
    argv = katana.build_argv(
        katana_bin="katana", domain="acme.io", scope_hosts=["acme.io"],
        depth=3, crawl_duration_seconds=120.0, system_chrome_path="/usr/bin/chromium",
    )
    assert argv[0] == "katana"
    assert "-headless" in argv and "-no-sandbox" in argv
    assert "-jsonl" in argv
    assert argv[argv.index("-u") + 1] == "https://acme.io"
    assert argv[argv.index("-system-chrome-path") + 1] == "/usr/bin/chromium"
    assert "-em" in argv and argv[argv.index("-em") + 1] == "js"


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
