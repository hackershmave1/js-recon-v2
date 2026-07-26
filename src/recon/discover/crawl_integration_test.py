"""Real katana+chromium crawl of a local fixture site (crawl+parse layer).

Runs where katana+chromium exist (the container/CI); SKIPS on a host without
katana. Verifies the real headless crawl discovers the fixture's linked .js.
progress.beat is patched so no DB/redis is needed — the heartbeat path is
unit-tested in harness_test.py. The egress-drop security behavior is asserted
separately (it cannot be exercised via discover_run against a private-IP fixture,
since egress.is_public_ip rejects RFC1918)."""
from unittest.mock import patch

import pytest

from recon.config import get_settings
from recon.discover import harness, katana
from recon.fetch import egress

pytestmark = pytest.mark.integration

FIXTURE_URL = "http://recon.test/"
SCOPE = ["recon.test"]


def test_real_katana_crawl_discovers_in_scope_js(engines_required):
    settings = get_settings()
    argv = katana.build_argv(
        katana_bin=settings.katana_bin, domain=FIXTURE_URL, scope_hosts=SCOPE,
        depth=settings.crawl_depth, crawl_duration_seconds=30.0,
    )
    try:
        with patch("recon.discover.harness.progress.beat"):
            result = harness.run_crawl(
                None, argv, tenant_id="t", run_id="r", job_id="j",
                duration_seconds=30.0, kill_grace_seconds=5.0,
                heartbeat_interval_seconds=5.0,
                max_output_bytes=settings.crawl_max_output_bytes,
            )
    except FileNotFoundError:
        if engines_required:
            raise
        pytest.skip("katana binary not available on this host")
    urls = katana.parse_assets(result.stdout)
    assert any(u.endswith("/app.js") for u in urls), urls
    assert any(u.endswith("/vendor.js") for u in urls), urls


def test_egress_drops_internal_and_out_of_scope_urls():
    # discover_run re-validates every katana URL through this guard before it can
    # enter the manifest; internal/out-of-scope URLs are always rejected.
    with pytest.raises(egress.EgressBlocked):
        egress.validate_target("http://169.254.169.254/meta.js", SCOPE)
    with pytest.raises(egress.EgressBlocked):
        egress.validate_target("http://evil.example/x.js", SCOPE)
