"""T-026 — ingestion idempotency & concurrent-write safety.

Regression coverage for the non-atomic check-then-insert in ``save_files``
(``app/api/routes/ingestion.py``). The per-file dedup is ``SELECT (session_id,
content_hash)`` then, on a miss, ``INSERT`` guarded by the
``files_session_content_unique`` constraint. Under two concurrent writers on the
same session both SELECT-miss and both INSERT; the loser used to raise an
unhandled ``IntegrityError`` that poisoned the transaction and aborted the whole
recon crawl. The fix wraps the INSERT in a SAVEPOINT and adopts the winner's row.

These run against the REAL database the app is configured for (Postgres): the ORM
uses ``postgresql.UUID`` / JSONB columns that SQLAlchemy cannot compile under the
SQLite fixture in ``conftest.py``. Skips cleanly when ``DATABASE_URL`` is unset.
Each test uses fresh random ids, so it is isolated from other rows.
"""
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
from app.db import SessionLocal
from app.models import File as DbFile
from app.api.routes.ingestion import FileIn, IngestionPayload, save_files
from sqlalchemy.exc import IntegrityError


client = TestClient(app)

CONTENT = "console.log('t026 idempotency');"


def _file_payload(session_id: str, content_hash: str, url: str | None = None) -> dict:
    return {
        "url": url or "https://example.com/t026.js",
        "contentHash": content_hash,
        "sessionId": session_id,
        "capturedAt": "2026-08-04T00:00:00Z",
        "contentType": "application/javascript",
        "contentEncoding": "identity",
        "contentLength": len(CONTENT.encode("utf-8")),
        "content": CONTENT,
        "dependencies": [],
    }


def _count_files(session_id: str, content_hash: str | None = None) -> int:
    db = SessionLocal()
    try:
        query = db.query(DbFile).filter(DbFile.session_id == uuid.UUID(session_id))
        if content_hash is not None:
            query = query.filter(DbFile.content_hash == content_hash)
        return query.count()
    finally:
        db.close()


def test_within_batch_duplicate_content_hash_is_deduped():
    """Two FileIn with the SAME (session_id, content_hash) in one batch → 200 and a
    single files row. Documents that within-batch dedup is already safe (the explicit
    flush makes read-your-writes work)."""
    session_id = str(uuid.uuid4())
    content_hash = f"t026-batch-{uuid.uuid4().hex}"
    payload = {
        "metadata": {"sessionId": session_id, "performAnalysis": False, "disableAnalysis": True},
        "files": [
            _file_payload(session_id, content_hash),
            _file_payload(session_id, content_hash),
        ],
    }

    resp = client.post("/api/save-files", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    # Both entries collapse onto one stored file id, and only one row exists.
    assert len(set(data["fileIds"])) == 1
    assert _count_files(session_id, content_hash) == 1


def test_extension_reupload_is_idempotent():
    """Re-posting the same (session_id, content_hash) reuses the row (extension
    re-capture), never duplicating it."""
    session_id = str(uuid.uuid4())
    content_hash = f"t026-reupload-{uuid.uuid4().hex}"
    payload = {
        "metadata": {"sessionId": session_id, "performAnalysis": False, "disableAnalysis": True},
        "files": [_file_payload(session_id, content_hash)],
    }

    first = client.post("/api/save-files", json=payload)
    assert first.status_code == 200, first.text
    first_id = first.json()["fileIds"][0]

    second = client.post("/api/save-files", json=payload)
    assert second.status_code == 200, second.text
    second_id = second.json()["fileIds"][0]

    assert first_id == second_id  # same row reused
    assert _count_files(session_id, content_hash) == 1


def test_savepoint_recovery_survives_real_unique_violation():
    """The savepoint-recovery mechanism the fix relies on, proven against real
    Postgres with a genuine UniqueViolation and no threads: a concurrent writer has
    committed the row; the loser's INSERT inside a SAVEPOINT collides; after catching
    IntegrityError and re-SELECTing, the loser's OUTER transaction (which carries
    other pending inserts, like save_files' child rows) stays alive and commits."""
    session_id = str(uuid.uuid4())
    winner_hash = f"t026-winner-{uuid.uuid4().hex}"
    other_hash = f"t026-other-{uuid.uuid4().hex}"

    # Seed the session + the winner file (committed) via the normal ingest path.
    seed = client.post(
        "/api/save-files",
        json={
            "metadata": {"sessionId": session_id, "performAnalysis": False, "disableAnalysis": True},
            "files": [_file_payload(session_id, winner_hash)],
        },
    )
    assert seed.status_code == 200, seed.text

    session_uuid = uuid.UUID(session_id)
    db = SessionLocal()
    try:
        # Unrelated batch work already pending in the loser's transaction — this must
        # survive the savepoint rollback below.
        db.add(
            DbFile(
                session_id=session_uuid,
                url="https://example.com/t026-other.js",
                content_hash=other_hash,
                content_length=len(CONTENT.encode("utf-8")),
                stored_path="/tmp/t026-other.js",
            )
        )
        db.flush()

        # The colliding insert (loser SELECT-missed earlier), wrapped in a SAVEPOINT.
        collided = False
        with_savepoint_dup = DbFile(
            session_id=session_uuid,
            url="https://example.com/t026.js",
            content_hash=winner_hash,
            content_length=len(CONTENT.encode("utf-8")),
            stored_path="/tmp/t026-dup.js",
        )
        try:
            with db.begin_nested():
                db.add(with_savepoint_dup)
                db.flush()
        except IntegrityError:
            collided = True
            adopted = (
                db.query(DbFile)
                .filter(DbFile.session_id == session_uuid, DbFile.content_hash == winner_hash)
                .first()
            )
            assert adopted is not None  # re-SELECT finds the winner's row

        assert collided is True, "expected a UniqueViolation on the duplicate insert"

        # Outer transaction survived the savepoint rollback: the unrelated work commits.
        db.commit()
    finally:
        db.close()

    assert _count_files(session_id, winner_hash) == 1  # dup did NOT create a second row
    assert _count_files(session_id, other_hash) == 1  # unrelated pending work survived


def test_concurrent_double_write_through_save_files_does_not_500():
    """End-to-end: two concurrent writers on the same (session_id, content_hash) drive
    a genuine TOCTOU through ``save_files``; the loser must degrade to idempotent reuse
    (no unhandled IntegrityError), leaving exactly one row.

    A holder thread inserts the row and keeps its transaction open (uncommitted) so the
    loser's own SELECT misses and its INSERT blocks on the unique index; the holder then
    commits, making the loser's flush raise — exercising the savepoint recovery. The
    asserted invariant (200-equivalent result + one row) holds even if timing makes the
    loser take the plain idempotent path instead, so the test is not flaky."""
    session_id = str(uuid.uuid4())
    content_hash = f"t026-concurrent-{uuid.uuid4().hex}"

    # Seed the session so neither path races on session creation (covered separately).
    seed = client.post(
        "/api/save-files",
        json={
            "metadata": {"sessionId": session_id, "performAnalysis": False, "disableAnalysis": True},
            "files": [_file_payload(session_id, f"t026-seed-{uuid.uuid4().hex}")],
        },
    )
    assert seed.status_code == 200, seed.text
    session_uuid = uuid.UUID(session_id)

    holder_ready = threading.Event()
    holder_error: list[Exception] = []

    def hold_uncommitted_row():
        db = SessionLocal()
        try:
            db.add(
                DbFile(
                    session_id=session_uuid,
                    url="https://example.com/t026.js",
                    content_hash=content_hash,
                    content_length=len(CONTENT.encode("utf-8")),
                    stored_path="/tmp/t026-holder.js",
                )
            )
            db.flush()  # take the row lock; keep the transaction open
            holder_ready.set()
            # Give the loser time to SELECT-miss and block on the unique index, then
            # commit so its blocked INSERT resolves to a UniqueViolation.
            import time

            time.sleep(1.5)
            db.commit()
        except Exception as exc:  # pragma: no cover - surfaced via holder_error
            holder_error.append(exc)
            db.rollback()
        finally:
            db.close()

    loser_result: dict = {}
    loser_error: list[Exception] = []

    def loser_save_files():
        holder_ready.wait(timeout=5)
        db = SessionLocal()
        try:
            payload = IngestionPayload(
                metadata={"sessionId": session_id, "performAnalysis": False, "disableAnalysis": True},
                files=[FileIn(**_file_payload(session_id, content_hash))],
            )
            loser_result["value"] = save_files(payload=payload, db=db)
        except Exception as exc:
            loser_error.append(exc)
        finally:
            db.close()

    holder = threading.Thread(target=hold_uncommitted_row, daemon=True)
    loser = threading.Thread(target=loser_save_files, daemon=True)
    holder.start()
    loser.start()
    holder.join(timeout=30)
    loser.join(timeout=30)

    assert not holder_error, f"holder thread failed: {holder_error}"
    assert not loser_error, f"save_files raised an unhandled error under concurrency: {loser_error}"
    assert loser_result.get("value", {}).get("stored") == 1
    assert _count_files(session_id, content_hash) == 1  # exactly one row despite the race
