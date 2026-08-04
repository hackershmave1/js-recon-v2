import os
from pathlib import Path

from ..config import settings


class StorageService:
    def __init__(self, base_path: str | None = None):
        self.base_path = Path(base_path or settings.storage_path)

    def ensure_session_paths(self, session_id: str) -> Path:
        session_dir = self.base_path / "sessions" / str(session_id)
        files_dir = session_dir / "files"
        maps_dir = session_dir / "maps"
        files_dir.mkdir(parents=True, exist_ok=True)
        maps_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def write_file(self, session_id: str, content_hash: str, content: str) -> str:
        self.ensure_session_paths(session_id)
        file_path = self.base_path / "sessions" / str(session_id) / "files" / f"{content_hash}.js"
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)

    def write_map(self, session_id: str, content_hash: str, content: str) -> str:
        self.ensure_session_paths(session_id)
        map_path = self.base_path / "sessions" / str(session_id) / "maps" / f"{content_hash}.map"
        map_path.write_text(content, encoding="utf-8")
        return str(map_path)
