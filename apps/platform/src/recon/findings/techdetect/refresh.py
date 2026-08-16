"""Re-pin the vendored enthec/webappanalyzer dataset (manual, Phase 1).

Run: ``uv run python -m recon.findings.techdetect.refresh <ref>`` — fetches the
enthec/webappanalyzer ``src/technologies/*.json`` + ``src/categories.json`` at a git
ref, merges them into the vendored ``technologies.json`` / ``categories.json``, and
writes the pinned sha to ``commit.txt``. GPL-3.0 stays server-side (T10). Network is
used ONLY here, never at request time; the load path (dataset.py) is offline."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

_RAW = "https://raw.githubusercontent.com/enthec/webappanalyzer/{ref}/src"
_LETTERS = "_abcdefghijklmnopqrstuvwxyz"
_DATA_DIR = Path(__file__).resolve().parent.parent / "techdetect_data"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed host, manual tool
        return bytes(resp.read())


def refresh(ref: str) -> None:
    merged: dict[str, object] = {}
    for letter in _LETTERS:
        merged.update(json.loads(_get(f"{_RAW.format(ref=ref)}/technologies/{letter}.json")))
    (_DATA_DIR / "technologies.json").write_text(
        json.dumps(merged, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )
    (_DATA_DIR / "categories.json").write_bytes(_get(f"{_RAW.format(ref=ref)}/categories.json"))
    (_DATA_DIR / "commit.txt").write_text(ref, encoding="utf-8")
    print(f"pinned enthec dataset at {ref}: {len(merged)} technologies")


if __name__ == "__main__":
    refresh(sys.argv[1] if len(sys.argv) > 1 else "master")
