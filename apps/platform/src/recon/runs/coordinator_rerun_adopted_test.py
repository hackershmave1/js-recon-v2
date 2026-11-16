"""ADR-0012: an adopted workspace session ends with a capture run; Re-run must still repeat the crawl."""

from unittest.mock import MagicMock, patch

import pytest

from recon.db.base import tenant_session
from recon.db.models import Run
from recon.runs import coordinator
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def test_rerun_skips_a_capture_run_and_repeats_the_latest_reproducible_one(tenant):
    sid = sessions_service.create_session(
        tenant, name="s", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    with tenant_session(tenant) as s:
        crawl = Run(tenant_id=tenant, session_id=sid, state="done", target="acme.io")
        s.add(crawl)
        s.flush()
        crawl_id = str(crawl.id)
        s.add(Run(tenant_id=tenant, session_id=sid, state="capturing"))  # the newer capture run
    with patch("recon.runs.coordinator.edit_and_rerun") as rerun:
        coordinator.rerun(MagicMock(), tenant_id=tenant, session_id=sid)
    assert rerun.call_args.kwargs["run_id"] == crawl_id


def test_rerun_of_a_session_with_only_captures_says_there_is_nothing_to_repeat(tenant):
    sid = sessions_service.create_session(
        tenant, name="s", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    with tenant_session(tenant) as s:
        s.add(Run(tenant_id=tenant, session_id=sid, state="done"))
    with pytest.raises(coordinator.NoRunToRerun):
        coordinator.rerun(MagicMock(), tenant_id=tenant, session_id=sid)
