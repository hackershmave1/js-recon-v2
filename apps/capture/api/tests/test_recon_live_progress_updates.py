import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import recon
from app.models import Job as DbJob


def _make_jobs_only_db():
    """Create an in-memory SQLite session with only the 'jobs' table.

    Other models (e.g. File) use JSONB which SQLite cannot render, so we
    create only the table we need rather than calling Base.metadata.create_all.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    DbJob.__table__.create(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def _insert_running_job(db, job_id: str, session_id: str) -> None:
    request = recon.ReconJobStartRequest(
        url="https://wishandwash.co.il",
        sessionId=session_id,
        discoveryEngine="katana",
        includeSourceMaps=True,
        performAnalysis=True,
    )
    state = recon.build_job_state(job_id, request, ["https://wishandwash.co.il"], session_id)
    state["status"] = "running"
    db_job = DbJob(
        id=uuid.UUID(job_id),
        job_type="recon",
        session_id=session_id,
        status="running",
        state_json=state,
    )
    db.add(db_job)
    db.commit()


def test_update_job_asset_recomputes_live_coverage():
    db = _make_jobs_only_db()
    job_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    _insert_running_job(db, job_id, session_id)

    recon.update_job_asset(
        job_id,
        {
            "url": "https://wishandwash.co.il/assets/app.js",
            "fetched": True,
            "ingested": True,
            "analyzed": False,
            "sourceMapDetectedUrl": "https://wishandwash.co.il/assets/app.js.map",
            "sourceMapFetched": True,
            "duplicateCount": 0,
            "failureReason": None,
        },
        db,
    )

    snapshot = recon.get_public_job_snapshot(job_id, db)
    coverage = snapshot["coverage"]
    assert coverage["discovered_js"] == 1
    assert coverage["fetched_js"] == 1
    assert coverage["ingested_js"] == 1
    assert coverage["analyzed_js"] == 0
    assert coverage["map_detected"] == 1
    assert coverage["map_fetched"] == 1
    assert snapshot["assetCount"] == 1


def test_latest_session_capture_coverage_reflects_running_job_state():
    db = _make_jobs_only_db()
    session_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    _insert_running_job(db, job_id, session_id)

    recon.update_job_asset(
        job_id,
        {
            "url": "https://wishandwash.co.il/assets/index.js",
            "fetched": True,
            "ingested": False,
            "analyzed": False,
            "sourceMapDetectedUrl": "https://wishandwash.co.il/assets/index.js.map",
            "sourceMapFetched": False,
            "duplicateCount": 0,
            "failureReason": None,
        },
        db,
    )

    coverage = recon.get_latest_session_capture_coverage(session_id, db)
    assert coverage is not None
    assert coverage["jobStatus"] == "running"
    assert coverage["discovered_js"] == 1
    assert coverage["fetched_js"] == 1
    assert coverage["map_detected"] == 1
    assert coverage["map_fetched"] == 0
