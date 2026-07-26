# src/recon/discover/crawl_test.py
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from recon.discover import crawl
from recon.discover.harness import CrawlResult
from recon.fetch import egress
from recon.queue import retry


def _patches(katana_urls, validated, engagement, existing=None):
    """Common patch set: session lookup, harness, katana parse, egress, storage, event."""
    def validate(url, scope):
        if url not in validated:
            raise egress.EgressBlocked(f"blocked: {url}")
        return SimpleNamespace(url=url)
    return [
        # Mock the DB seam so these stay pure units (record_event is patched too).
        patch("recon.discover.crawl.tenant_session"),
        patch("recon.discover.crawl.queries.latest_assets_event", return_value=existing),
        patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1")),
        patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement),
        patch("recon.discover.crawl.harness.run_crawl",
              return_value=CrawlResult(stdout=b"", timed_out=False)),
        patch("recon.discover.crawl.katana.parse_assets", return_value=katana_urls),
        patch("recon.discover.crawl.egress.validate_target", side_effect=validate),
        patch("recon.discover.crawl.storage.put_blob", return_value="t/r/assets/deadbeef"),
    ]


def test_discover_run_writes_only_in_scope_assets():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    recorded = {}
    with patch("recon.discover.crawl.record_event",
               side_effect=lambda *a, **k: recorded.update(k) or MagicMock()), \
         patch("recon.discover.crawl.publish"):
        for p in _patches(
            katana_urls=["https://acme.io/app.js", "http://169.254.169.254/x.js"],
            validated={"https://acme.io/app.js"}, engagement=engagement,
        ):
            p.start()
        try:
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
        finally:
            patch.stopall()
    # The internal/out-of-scope URL was dropped by egress re-validation.
    assert recorded["payload"]["count"] == 1
    assert recorded["payload"]["status"] == "ok"


def test_discover_run_is_idempotent_when_event_exists():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    with patch("recon.discover.crawl.harness.run_crawl") as run_crawl, \
         patch("recon.discover.crawl.queries.latest_assets_event",
               return_value={"count": 3, "assets_ref": "x", "status": "ok"}):
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()  # no re-crawl on redelivery


def test_discover_run_rejects_unauthorized_session():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=False)
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1")), \
         patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement):
        with pytest.raises(retry.FatalError):
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")


def test_discover_run_skips_target_with_path():
    # A single-asset URL target is NOT a crawl — no event, no rows (backward compat).
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target", return_value=("https://acme.io/app.js", "sess-1")), \
         patch("recon.discover.crawl.sessions_service.get_session"), \
         patch("recon.discover.crawl.harness.run_crawl") as run_crawl:
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()


def test_discover_run_seeds_run_asset_rows():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    seeded = {}
    with patch("recon.discover.crawl.record_event", return_value=MagicMock()), \
         patch("recon.discover.crawl.publish"), \
         patch("recon.discover.crawl.assets.seed_pending",
               side_effect=lambda s, **k: seeded.update(k)):
        for p in _patches(
            katana_urls=["https://acme.io/app.js", "https://acme.io/vendor.js"],
            validated={"https://acme.io/app.js", "https://acme.io/vendor.js"},
            engagement=engagement,
        ):
            p.start()
        try:
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
        finally:
            patch.stopall()
    assert seeded["urls"] == ["https://acme.io/app.js", "https://acme.io/vendor.js"]
