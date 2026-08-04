from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import retention_cleanup


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeRow:
    def __init__(self):
        self.content_purged = False
        self.content_purged_at = None
        self.purge_reason = None


class _FakeSession:
    def __init__(self, file_rows, map_rows):
        self._file_rows = file_rows
        self._map_rows = map_rows
        self.commits = 0

    def query(self, model):
        if model is retention_cleanup.DbFile:
            return _FakeQuery(self._file_rows)
        if model is retention_cleanup.DbSourceMap:
            return _FakeQuery(self._map_rows)
        return _FakeQuery([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _write_old_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    ts = (datetime.now(timezone.utc) - timedelta(days=45)).timestamp()
    import os

    os.utime(path, (ts, ts))


def test_cleanup_marks_purge_metadata_when_deleting(tmp_path, monkeypatch):
    file_path = tmp_path / "sessions" / "session-1" / "files" / "sample.js"
    map_path = tmp_path / "sessions" / "session-1" / "maps" / "sample.map"
    _write_old_file(file_path)
    _write_old_file(map_path)

    file_row = _FakeRow()
    map_row = _FakeRow()
    fake_session = _FakeSession([file_row], [map_row])
    monkeypatch.setattr(retention_cleanup, "SessionLocal", lambda: fake_session)

    result = retention_cleanup.run_retention_cleanup(
        base_path=str(tmp_path),
        file_ttl_days=1,
        sourcemap_ttl_days=1,
        dry_run=False,
        max_deletions=10,
    )

    assert result["summary"]["deleted"] == 2
    assert result["summary"]["purgeMarkersUpdated"] >= 2
    assert result["summary"]["purgeMarkersSkipped"] is False
    assert file_row.content_purged is True
    assert map_row.content_purged is True
    assert file_row.purge_reason == "retention_ttl_expired"
    assert map_row.purge_reason == "retention_ttl_expired"
    assert file_path.exists() is False
    assert map_path.exists() is False
