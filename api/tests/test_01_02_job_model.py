"""
TDD tests for the Job ORM model (plan 01-02).

RED phase: these tests fail until api/app/models/job.py is created.
"""
import sys
import pytest
from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.orm import sessionmaker


# ---------------------------------------------------------------------------
# Test: importability and class attributes
# ---------------------------------------------------------------------------

def test_job_model_importable():
    """Job class must be importable from app.models.job."""
    from app.models.job import Job  # noqa: F401
    assert Job is not None


def test_job_exportable_from_models():
    """Job must be re-exported from app.models."""
    from app.models import Job  # noqa: F401
    assert Job is not None


def test_job_tablename():
    """Job.__tablename__ must be 'jobs'."""
    from app.models.job import Job
    assert Job.__tablename__ == "jobs"


def test_job_in_all():
    """'Job' must appear in app.models.__all__."""
    import app.models as m
    assert "Job" in m.__all__


# ---------------------------------------------------------------------------
# Test: column presence and basic properties
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {
    "id",
    "job_type",
    "session_id",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "cancel_requested",
    "cancel_requested_at",
    "error",
    "state_json",
}


def test_job_required_columns():
    """All 11 required columns must be present on the Job mapper."""
    from app.models.job import Job
    cols = {c.key for c in sa_inspect(Job).mapper.column_attrs}
    missing = REQUIRED_COLUMNS - cols
    assert not missing, f"Missing columns: {missing}"


def test_job_state_json_is_json_type():
    """state_json column type must be JSON (not Text)."""
    from app.models.job import Job
    from sqlalchemy.types import JSON
    mapper = sa_inspect(Job).mapper
    col = mapper.columns["state_json"]
    assert isinstance(col.type, JSON), f"state_json type is {type(col.type)}"


def test_job_session_id_is_indexed():
    """session_id column must have index=True."""
    from app.models.job import Job
    mapper = sa_inspect(Job).mapper
    col = mapper.columns["session_id"]
    assert col.index is True, "session_id should have index=True"


# ---------------------------------------------------------------------------
# Test: default values and construction
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sqlite_session():
    """In-memory SQLite session for constructor tests."""
    # Import here so failure is captured in the test that uses this fixture
    from app.db import Base
    # Must import Job so it registers with Base metadata
    from app.models.job import Job  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    Base.metadata.drop_all(bind=engine)


def test_job_recon_construction(sqlite_session):
    """Job('recon', ...) can be constructed and flushed."""
    from app.models.job import Job
    job = Job(job_type="recon", session_id="session-uuid-001")
    sqlite_session.add(job)
    sqlite_session.flush()
    assert job.id is not None
    assert job.status == "queued"
    sqlite_session.rollback()


def test_job_session_analysis_construction(sqlite_session):
    """Job('session_analysis', ...) can be constructed and flushed."""
    from app.models.job import Job
    job = Job(job_type="session_analysis", session_id="session-uuid-002")
    sqlite_session.add(job)
    sqlite_session.flush()
    assert job.id is not None
    assert job.status == "queued"
    sqlite_session.rollback()


def test_job_state_json_defaults_to_dict(sqlite_session):
    """job.state_json must be a dict (not None) after flush."""
    from app.models.job import Job
    job = Job(job_type="recon", session_id="session-uuid-003")
    sqlite_session.add(job)
    sqlite_session.flush()
    assert isinstance(job.state_json, dict), f"state_json={job.state_json!r}"
    sqlite_session.rollback()


def test_job_id_is_uuid(sqlite_session):
    """job.id must be a UUID object after flush."""
    import uuid
    from app.models.job import Job
    job = Job(job_type="recon")
    sqlite_session.add(job)
    sqlite_session.flush()
    assert isinstance(job.id, uuid.UUID), f"id type={type(job.id)}"
    sqlite_session.rollback()
