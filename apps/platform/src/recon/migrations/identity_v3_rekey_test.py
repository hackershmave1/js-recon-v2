"""The v2 -> v3 re-key map behind migration 0033 (pure; no DB)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from recon.findings import normalize

_PATH = Path(__file__).parent / "versions" / "0033_identity_v3_rekey.py"
_SPEC = importlib.util.spec_from_file_location("identity_v3_rekey", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _v2(finding_type: str, value: str) -> tuple[str, str, str]:
    return migration._hash(2, finding_type, value), finding_type, value


def test_mapped_and_minified_v2_endpoints_rekey_to_the_one_current_v3_finding():
    mapped, minified = _v2("endpoint", "GET /u/${userId}"), _v2("endpoint", "GET /u/${e}")
    mapping = migration.v3_rekey_map([mapped, minified])
    current = normalize.finding_hash("endpoint", "GET /u/{id}")
    assert mapping == {mapped[0]: current, minified[0]: current}


def test_unchanged_values_still_rekey_because_the_version_is_hashed():
    secret = _v2("secret", "aws:abc")
    assert migration.v3_rekey_map([secret]) == {
        secret[0]: normalize.finding_hash("secret", "aws:abc")
    }


def test_non_v2_rows_and_fake_query_params_are_skipped():
    already_v3 = (normalize.finding_hash("endpoint", "GET /x"), "endpoint", "GET /x")
    fake = _v2("param", "POST /x query:${new URLSearchParams(r)}")
    assert migration.v3_rekey_map([already_v3, fake]) == {}
