from unittest.mock import MagicMock, patch

from recon.domain import RunStage
from recon.worker import main


def test_discovering_stage_calls_discover_run():
    called = {}
    with patch(
        "recon.worker.main.crawl.discover_run", side_effect=lambda *a, **k: called.update(k)
    ):
        main._run_stage_work(
            MagicMock(), RunStage.DISCOVERING, tenant_id="t", run_id="r", job_id="j"
        )
    assert called == {"tenant_id": "t", "run_id": "r", "job_id": "j"}
