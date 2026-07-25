from recon.config import get_settings
from recon import storage


def test_crawl_settings_have_defaults():
    s = get_settings()
    assert s.crawl_depth == 3
    assert s.crawl_max_assets == 500
    assert s.katana_bin == "katana"
    # Lease renewal invariant: a heartbeat must fire well within the stall window.
    assert s.crawl_heartbeat_interval_seconds < s.heartbeat_stall_threshold_seconds


def test_assets_is_a_valid_blob_kind():
    key = storage.object_key("t-1", "r-1", "assets", b"{}")
    assert key.split("/")[2] == "assets"
