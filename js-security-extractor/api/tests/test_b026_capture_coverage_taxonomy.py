import os
import uuid

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app
from app.api.routes import recon


def _upload_single_file(client: TestClient, session_id: str) -> None:
    content = "function b026(){ return '/api/b026'; } b026();"
    payload = {
        "metadata": {
            "sessionId": session_id,
            "performAnalysis": False,
        },
        "files": [
            {
                "url": "https://wishandwash.co.il/assets/b026.js",
                "contentHash": f"b026-hash-{session_id[:8]}",
                "sessionId": session_id,
                "capturedAt": "2026-02-10T20:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(content.encode("utf-8")),
                "content": content,
                "dependencies": [],
            }
        ],
    }
    response = client.post("/api/save-files", json=payload)
    assert response.status_code == 200


def _find_session_row(client: TestClient, session_id: str) -> dict:
    response = client.get("/api/sessions")
    assert response.status_code == 200
    for row in response.json():
        if row.get("id") == session_id:
            return row
    raise AssertionError(f"Session {session_id} not found")


def test_build_coverage_snapshot_normalizes_taxonomy():
    snapshot = recon.build_coverage_snapshot(
        {
            "discovered_js": 10,
            "fetched_js": 7,
            "ingested_js": 6,
            "analyzed_js": 4,
            "map_detected": 3,
            "map_fetched": 2,
            "failure_reasons": {
                "fetch_4xx": 2,
                "not_seen": 1,
                "unknown_reason": 99,
            },
        }
    )

    expected_keys = set(recon.MISS_REASON_TAXONOMY)
    assert set(snapshot["failure_reasons"].keys()) == expected_keys
    assert snapshot["failure_reasons"]["fetch_4xx"] == 2
    assert snapshot["failure_reasons"]["not_seen"] == 1
    assert snapshot["failure_reasons"]["parse_failed"] == 0
    assert snapshot["map_processed"] == 2
    assert snapshot["rates"]["analysisPct"] == 40.0


def test_sessions_list_includes_latest_capture_coverage():
    client = TestClient(app)
    session_id = str(uuid.uuid4())
    _upload_single_file(client, session_id)

    with recon.RECON_LOCK:
        recon.RECON_JOBS.clear()
        recon.RECON_JOB_STOP_EVENTS.clear()
        recon.RECON_JOBS["job-old"] = {
            "jobId": "job-old",
            "sessionId": session_id,
            "status": "completed",
            "createdAt": "2026-02-10T10:00:00Z",
            "startedAt": "2026-02-10T10:00:02Z",
            "finishedAt": "2026-02-10T10:01:00Z",
            "coverage": {
                "discovered_js": 2,
                "fetched_js": 2,
                "ingested_js": 2,
                "analyzed_js": 2,
                "map_detected": 1,
                "map_fetched": 1,
                "failure_reasons": {"not_seen": 0},
            },
        }
        recon.RECON_JOBS["job-new"] = {
            "jobId": "job-new",
            "sessionId": session_id,
            "status": "completed",
            "createdAt": "2026-02-10T11:00:00Z",
            "startedAt": "2026-02-10T11:00:02Z",
            "finishedAt": "2026-02-10T11:02:00Z",
            "coverage": {
                "discovered_js": 5,
                "fetched_js": 4,
                "ingested_js": 4,
                "analyzed_js": 3,
                "map_detected": 2,
                "map_fetched": 1,
                "failure_reasons": {"fetch_4xx": 1, "not_seen": 1},
            },
        }

    row = _find_session_row(client, session_id)
    capture = row.get("captureCoverage") or {}
    assert capture.get("jobId") == "job-new"
    assert capture.get("jobStatus") == "completed"
    assert int(capture.get("discovered_js") or 0) == 5
    assert int(capture.get("map_processed") or 0) == 1
    reasons = capture.get("failure_reasons") or {}
    assert set(reasons.keys()) == set(recon.MISS_REASON_TAXONOMY)
    assert int(reasons.get("fetch_4xx") or 0) == 1

