import uuid

from app.api.routes import recon


def make_running_job(job_id: str, session_id: str) -> dict:
    request = recon.ReconJobStartRequest(
        url="https://wishandwash.co.il",
        sessionId=session_id,
        discoveryEngine="katana",
        includeSourceMaps=True,
        performAnalysis=True,
    )
    job = recon.build_job_state(job_id, request, ["https://wishandwash.co.il"], session_id)
    job["status"] = "running"
    return job


def test_update_job_asset_recomputes_live_coverage():
    job_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    with recon.RECON_LOCK:
        recon.RECON_JOBS.clear()
        recon.RECON_JOB_STOP_EVENTS.clear()
        recon.RECON_JOBS[job_id] = make_running_job(job_id, session_id)

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
    )

    snapshot = recon.get_public_job_snapshot(job_id)
    coverage = snapshot["coverage"]
    assert coverage["discovered_js"] == 1
    assert coverage["fetched_js"] == 1
    assert coverage["ingested_js"] == 1
    assert coverage["analyzed_js"] == 0
    assert coverage["map_detected"] == 1
    assert coverage["map_fetched"] == 1
    assert snapshot["assetCount"] == 1


def test_latest_session_capture_coverage_reflects_running_job_state():
    session_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    with recon.RECON_LOCK:
        recon.RECON_JOBS.clear()
        recon.RECON_JOB_STOP_EVENTS.clear()
        recon.RECON_JOBS[job_id] = make_running_job(job_id, session_id)

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
    )

    coverage = recon.get_latest_session_capture_coverage(session_id)
    assert coverage is not None
    assert coverage["jobStatus"] == "running"
    assert coverage["discovered_js"] == 1
    assert coverage["fetched_js"] == 1
    assert coverage["map_detected"] == 1
    assert coverage["map_fetched"] == 0
