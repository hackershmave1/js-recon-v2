"""Quarantine stays unreadable by construction (ADR-0010): only the quarantine feature, the model
and the migrations may name its tables. Any other module that reads them would be a reader of
quarantined content, so this fails the build the day someone adds one."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_NAMES = ("QuarantinedAsset", "quarantined_asset", "HostDecision", "host_decision")
_ALLOWED_DIRS = {ROOT / "quarantine", ROOT / "migrations"}
_ALLOWED_FILES = {ROOT / "db" / "models.py", ROOT / "domain.py"}  # domain: the state enum


def _allowed(path: Path) -> bool:
    return path in _ALLOWED_FILES or any(parent in _ALLOWED_DIRS for parent in path.parents)


def test_only_the_quarantine_feature_names_its_tables():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if _allowed(path):
            continue
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in _NAMES):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"modules outside the quarantine feature read its tables: {offenders}"


def test_the_quarantine_blob_kind_is_its_own_prefix():
    from recon import storage

    assert "quarantine" in storage.BLOB_KINDS
    key = storage.object_key("t", "r", "quarantine", b"x")
    assert key.split("/")[2] == "quarantine"
