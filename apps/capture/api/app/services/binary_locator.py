from __future__ import annotations

import os
import shutil
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def candidate_binary_paths(binary_name: str, env_var: str | None = None) -> list[str]:
    candidates: list[str] = []

    if env_var:
        env_value = (os.getenv(env_var) or "").strip()
        if env_value:
            candidates.append(env_value)

    which_path = shutil.which(binary_name)
    if which_path:
        candidates.append(which_path)

    candidates.extend(
        [
            str(API_ROOT / ".tools" / "bin" / binary_name),
            str(REPO_ROOT / ".tools" / "bin" / binary_name),
            str(Path.home() / ".local" / "bin" / binary_name),
            f"/usr/local/bin/{binary_name}",
            f"/usr/bin/{binary_name}",
        ]
    )

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = (candidate or "").strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def resolve_binary_path(binary_name: str, env_var: str | None = None) -> str | None:
    for candidate in candidate_binary_paths(binary_name, env_var=env_var):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None

