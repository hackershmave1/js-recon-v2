from datetime import datetime
from types import SimpleNamespace

from app.services.job_recovery import recover_orphaned_jobs


class FakeQuery:
    def __init__(self, jobs):
        self.jobs = jobs

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.jobs


class FakeSession:
    def __init__(self, jobs):
        self.jobs = jobs
        self.committed = False

    def query(self, _model):
        return FakeQuery(self.jobs)

    def commit(self):
        self.committed = True


def test_recover_orphaned_jobs_marks_active_jobs_terminal():
    recovered_at = datetime(2026, 6, 16, 12, 0, 0)
    running = SimpleNamespace(
        status="running",
        cancel_requested=False,
        error=None,
        finished_at=None,
        state_json={"status": "running"},
    )
    cancelling = SimpleNamespace(
        status="cancelling",
        cancel_requested=True,
        error=None,
        finished_at=None,
        state_json={"status": "cancelling"},
    )
    db = FakeSession([running, cancelling])

    assert recover_orphaned_jobs(db, recovered_at=recovered_at) == 2

    assert db.committed is True
    assert running.status == "failed"
    assert running.finished_at == recovered_at
    assert running.state_json["startup_recovered"] is True
    assert cancelling.status == "cancelled"
    assert cancelling.finished_at == recovered_at
