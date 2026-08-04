import asyncio
import os
import threading
import uuid

import pytest
from fastapi.testclient import TestClient


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-test-{uuid.uuid4()}")

from app.main import app
from app.api.routes import recon
from app.services import recon_job_runner


class TestReconJobApi:
    def setup_method(self):
        self.client = TestClient(app)
        with recon.RECON_LOCK:
            recon.RECON_JOBS.clear()
            recon.RECON_JOB_STOP_EVENTS.clear()

    def test_start_requires_at_least_one_url(self):
        response = self.client.post("/api/recon/jobs/start", json={})
        assert response.status_code == 400
        assert "At least one target URL is required" in response.json()["detail"]

    def test_start_rejects_invalid_url(self):
        response = self.client.post(
            "/api/recon/jobs/start",
            json={"url": "ftp://wishandwash.co.il/app.js"},
        )
        assert response.status_code == 422
        assert "Invalid target URL" in response.json()["detail"]

    def test_start_rejects_invalid_discovery_engine(self):
        response = self.client.post(
            "/api/recon/jobs/start",
            json={
                "url": "https://wishandwash.co.il",
                "discoveryEngine": "invalid-engine",
            },
        )
        assert response.status_code == 422
        assert "Invalid discoveryEngine" in response.json()["detail"]

    def test_start_rejects_katana_when_binary_missing(self, monkeypatch):
        monkeypatch.setattr(recon.shutil, "which", lambda _: None)
        response = self.client.post(
            "/api/recon/jobs/start",
            json={
                "url": "https://wishandwash.co.il",
                "discoveryEngine": "katana",
            },
        )
        assert response.status_code == 422
        assert "katana binary is not available" in response.json()["detail"]

    def test_start_and_read_completed_job_snapshot(self, monkeypatch):
        original_thread_cls = threading.Thread

        def fake_worker(job_id, options, worker_session_factory):
            with recon.RECON_LOCK:
                job = recon.RECON_JOBS[job_id]
                job["status"] = "completed"
                job["startedAt"] = recon.now_iso()
                job["finishedAt"] = recon.now_iso()
                url = options.urls[0]
                job["assets"] = {
                    url: {
                        "url": url,
                        "targetUrl": url,
                        "discoveryMethod": "headless_response",
                        "depth": 0,
                        "discovered": True,
                        "fetched": True,
                        "ingested": True,
                        "analyzed": True,
                        "dedupSkipped": False,
                        "discoveredAt": recon.now_iso(),
                        "failureReason": None,
                        "error": None,
                        "httpStatus": 200,
                        "contentType": "application/javascript",
                        "contentLength": 1200,
                        "analysisStatus": "completed",
                        "sourceMapDetectedUrl": f"{url}.map",
                        "sourceMapFetched": True,
                        "sourceMapError": None,
                        "fileId": str(uuid.uuid4()),
                    }
                }
                job["coverage"] = {
                    "discovered_js": 1,
                    "fetched_js": 1,
                    "ingested_js": 1,
                    "analyzed_js": 1,
                    "map_detected": 1,
                    "map_fetched": 1,
                    "map_failed": 0,
                    "failure_reasons": {},
                }
                job["summary"] = {
                    "stored": 1,
                    "fileIds": [str(uuid.uuid4())],
                    "cancelled": False,
                }

        class ImmediateReconThread:
            def __init__(self, *args, **kwargs):
                self._target = kwargs.get("target")
                self._args = kwargs.get("args") or ()
                self._kwargs = kwargs.get("kwargs") or {}
                self._daemon = kwargs.get("daemon")
                self._inner = original_thread_cls(*args, **kwargs)

            def start(self):
                if self._target is fake_worker:
                    self._target(*self._args, **self._kwargs)
                else:
                    self._inner.start()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(recon, "run_recon_job_worker", fake_worker)
        monkeypatch.setattr(recon.threading, "Thread", ImmediateReconThread)

        response = self.client.post(
            "/api/recon/jobs/start",
            json={
                "url": "https://wishandwash.co.il/assets/index-BDSyL5Fh.js",
                "maxAssets": 25,
                "performAnalysis": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        job_id = body["jobId"]

        status_response = self.client.get(f"/api/recon/jobs/{job_id}")
        assert status_response.status_code == 200
        job = status_response.json()["job"]
        assert job["status"] == "completed"
        assert job["coverage"]["discovered_js"] == 1
        assert job["assetCount"] == 1
        assert job["summary"]["stored"] == 1

    def test_stop_sets_cancel_request_for_queued_job(self, monkeypatch):
        original_thread_cls = threading.Thread
        route_worker = recon.run_recon_job_worker

        class NoopReconThread:
            def __init__(self, *args, **kwargs):
                self._target = kwargs.get("target")
                self._inner = original_thread_cls(*args, **kwargs)

            def start(self):
                if self._target is route_worker:
                    return None
                self._inner.start()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(recon.threading, "Thread", NoopReconThread)

        start_response = self.client.post(
            "/api/recon/jobs/start",
            json={"url": "https://wishandwash.co.il/assets/index-BDSyL5Fh.js"},
        )
        assert start_response.status_code == 200
        job_id = start_response.json()["jobId"]

        stop_response = self.client.post(f"/api/recon/jobs/{job_id}/stop")
        assert stop_response.status_code == 200
        body = stop_response.json()
        assert body["success"] is True
        assert body["stopRequested"] is True
        assert body["job"]["cancelRequested"] is True

    def test_start_creates_named_session_for_katana_recon(self, monkeypatch):
        original_thread_cls = threading.Thread
        route_worker = recon.run_recon_job_worker

        class NoopReconThread:
            def __init__(self, *args, **kwargs):
                self._target = kwargs.get("target")
                self._inner = original_thread_cls(*args, **kwargs)

            def start(self):
                if self._target is route_worker:
                    return None
                self._inner.start()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(recon.threading, "Thread", NoopReconThread)
        monkeypatch.setattr(recon.shutil, "which", lambda _: "/usr/bin/katana")

        session_id = str(uuid.uuid4())
        response = self.client.post(
            "/api/recon/jobs/start",
            json={
                "sessionId": session_id,
                "sessionName": "wishandwash katana run",
                "url": "https://wishandwash.co.il",
                "discoveryEngine": "katana",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["sessionId"] == session_id
        assert payload["sessionCreated"] is True
        assert payload["job"]["options"]["discoveryEngine"] == "katana"

        sessions_response = self.client.get("/api/sessions")
        assert sessions_response.status_code == 200
        sessions = sessions_response.json()
        created = next((row for row in sessions if row.get("id") == session_id), None)
        assert created is not None
        assert created["name"] == "wishandwash katana run"
        assert created["source"] == "recon_katana"

    def test_runner_ingests_in_batches_during_prepare(self, monkeypatch):
        options = recon.ReconRunnerOptions(
            urls=["https://wishandwash.co.il"],
            session_id=str(uuid.uuid4()),
            same_origin_only=False,
            include_sourcemaps=False,
            perform_analysis=False,
            ingest_batch_size=2,
        )
        runner = recon.ReconJobRunner(options=options, db=None)

        async def fake_discover_target(target_url: str):
            runner._register_candidate("https://wishandwash.co.il/assets/a.js", target_url, "katana", 0)
            runner._register_candidate("https://wishandwash.co.il/assets/b.js", target_url, "katana", 0)
            runner._register_candidate("https://wishandwash.co.il/assets/c.js", target_url, "katana", 0)

        async def fake_fetch_text(url: str):
            return {
                "success": True,
                "statusCode": 200,
                "contentType": "application/javascript",
                "headers": {},
                "content": f"console.log('{url}')",
                "finalUrl": url,
            }

        saved_batches: list[list[str]] = []

        def fake_save_files(payload, db):
            urls = [row.url for row in payload.files]
            saved_batches.append(urls)
            file_ids = [str(uuid.uuid4()) for _ in payload.files]
            files = [
                {
                    "url": row.url,
                    "fileId": file_ids[idx],
                    "analysis": {"status": "skipped"},
                }
                for idx, row in enumerate(payload.files)
            ]
            return {
                "success": True,
                "stored": len(payload.files),
                "files": files,
                "fileIds": file_ids,
            }

        monkeypatch.setattr(runner, "_discover_target", fake_discover_target)
        monkeypatch.setattr(runner, "_fetch_text", fake_fetch_text)
        monkeypatch.setattr(recon_job_runner, "save_files", fake_save_files)

        result = asyncio.run(runner.run())

        assert len(saved_batches) == 2
        assert saved_batches[0] == [
            "https://wishandwash.co.il/assets/a.js",
            "https://wishandwash.co.il/assets/b.js",
        ]
        assert saved_batches[1] == ["https://wishandwash.co.il/assets/c.js"]
        assert result["ingestion"]["stored"] == 3
        assert result["coverage"]["fetched_js"] == 3
        assert result["coverage"]["ingested_js"] == 3

    def test_runner_retries_without_analysis_on_jsonb_overflow(self, monkeypatch):
        options = recon.ReconRunnerOptions(
            urls=["https://wishandwash.co.il"],
            session_id=str(uuid.uuid4()),
            same_origin_only=False,
            include_sourcemaps=False,
            perform_analysis=True,
            ingest_batch_size=10,
        )
        runner = recon.ReconJobRunner(options=options, db=None)

        async def fake_discover_target(target_url: str):
            runner._register_candidate("https://wishandwash.co.il/assets/huge.js", target_url, "katana", 0)

        async def fake_fetch_text(url: str):
            return {
                "success": True,
                "statusCode": 200,
                "contentType": "application/javascript",
                "headers": {},
                "content": "console.log('huge-analysis-payload');",
                "finalUrl": url,
            }

        analysis_modes: list[tuple[bool, bool]] = []

        def fake_save_files(payload, db):
            perform_analysis = bool((payload.metadata or {}).get("performAnalysis"))
            disable_analysis = bool((payload.metadata or {}).get("disableAnalysis"))
            analysis_modes.append((perform_analysis, disable_analysis))
            if perform_analysis:
                raise RuntimeError(
                    "psycopg2.errors.ProgramLimitExceeded: total size of jsonb array elements exceeds the maximum"
                )
            return {
                "success": True,
                "stored": 1,
                "files": [
                    {
                        "url": payload.files[0].url,
                        "fileId": str(uuid.uuid4()),
                        "analysis": {"status": "skipped"},
                    }
                ],
                "fileIds": [str(uuid.uuid4())],
            }

        monkeypatch.setattr(runner, "_discover_target", fake_discover_target)
        monkeypatch.setattr(runner, "_fetch_text", fake_fetch_text)
        monkeypatch.setattr(recon_job_runner, "save_files", fake_save_files)

        result = asyncio.run(runner.run())

        assert analysis_modes == [(True, False), (False, True)]
        assert result["ingestion"]["stored"] == 1
        assert result["coverage"]["fetched_js"] == 1
        assert result["coverage"]["ingested_js"] == 1

    def test_katana_command_does_not_use_extension_match_filter(self, monkeypatch):
        options = recon.ReconRunnerOptions(
            urls=["https://wishandwash.co.il"],
            session_id=str(uuid.uuid4()),
            discovery_engine="katana",
            max_depth=1,
            timeout_seconds=10,
        )
        runner = recon.ReconJobRunner(options=options, db=None)
        captured_args: dict[str, tuple] = {}

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                payload = (
                    '{"request":{"endpoint":"https://wishandwash.co.il/assets/index-BDSyL5Fh.js"}}\n'
                    '{"request":{"endpoint":"https://wishandwash.co.il/about"}}\n'
                )
                return payload.encode("utf-8"), b""

            def kill(self):
                return None

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured_args["args"] = args
            return FakeProcess()

        monkeypatch.setattr(recon_job_runner.shutil, "which", lambda _: "/root/go/bin/katana")
        monkeypatch.setattr(recon_job_runner.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        discovered = asyncio.run(runner._discover_with_katana("https://wishandwash.co.il"))

        command_args = list(captured_args.get("args") or [])
        assert "-em" not in command_args
        assert "https://wishandwash.co.il/assets/index-BDSyL5Fh.js" in discovered
        assert "https://wishandwash.co.il/about" not in discovered
