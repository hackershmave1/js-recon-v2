"""Tests for the Kingfisher adapter.

The parser tests use an inline JSONL fixture modeled on real
``kingfisher scan --format json`` output (the matched snippet is scrubbed so no
secret-shaped literal is committed). One integration test runs the real binary;
it skips cleanly if Kingfisher is not installed.
"""

from __future__ import annotations

import json
import sys

import pytest

from recon.findings import engines, kingfisher, normalize

# Modeled on real kingfisher-bin==1.106.0 output: a findings line (findings is a
# LIST) plus a summary line (findings is an INT). Snippet is a scrubbed stand-in.
_FINDINGS_LINE = json.dumps(
    {
        "findings": [
            {
                "rule": {"id": "kingfisher.stripe.2", "name": "Stripe Secret / Restricted Key"},
                "finding": {
                    "snippet": "SCRUBBED_TEST_TOKEN",
                    "fingerprint": "7432217094807587630",
                    "confidence": "medium",
                    "entropy": "5.01",
                    "language": "JavaScript",
                    "line": 4,
                    "column_start": 11,
                    "column_end": 52,
                    "path": "/tmp/kf/input.js",
                    "validation": {"status": "Not Attempted", "response": ""},
                },
            }
        ],
        "metadata": {"num_findings": 1},
    }
)
_SUMMARY_LINE = json.dumps(
    {"findings": 1, "kingfisher": "1.106.0", "findings_by_rule": [], "bytes_scanned": 42}
)
_JSONL = (_FINDINGS_LINE + "\n" + _SUMMARY_LINE + "\n").encode("utf-8")


def test_parse_findings_extracts_one_secret():
    secrets = kingfisher.parse_findings(_JSONL)
    assert len(secrets) == 1
    secret = secrets[0]
    assert secret.rule_id == "kingfisher.stripe.2"
    assert secret.rule_name == "Stripe Secret / Restricted Key"
    assert secret.snippet == "SCRUBBED_TEST_TOKEN"
    assert secret.confidence == "medium"
    assert secret.fingerprint == "7432217094807587630"
    assert secret.line == 4
    assert secret.column_start == 11
    assert secret.validation_status == "Not Attempted"


def test_parse_findings_skips_summary_and_noise_lines():
    noisy = b" INFO kingfisher: Loaded 930 rules\n" + _JSONL + b"not json at all\n"
    # The summary line (findings is an int) and log lines must not become secrets.
    assert len(kingfisher.parse_findings(noisy)) == 1


def test_parse_findings_skips_entries_missing_rule_or_snippet():
    envelope = json.dumps(
        {
            "findings": [
                {"rule": {"id": "kingfisher.aws.1"}, "finding": {}},
                {"finding": {"snippet": "x"}},
            ]
        }
    ).encode("utf-8")
    assert kingfisher.parse_findings(envelope) == []


def test_parse_findings_empty_output_is_empty():
    assert kingfisher.parse_findings(b"") == []


def test_byte_offset_is_deterministic_and_distinct():
    source = "a\nbb\nccc\n"  # line starts at bytes 0, 2, 5
    assert kingfisher.byte_offset(source, 1, 0) == 0
    assert kingfisher.byte_offset(source, 2, 1) == 3
    assert kingfisher.byte_offset(source, 3, 0) == 5
    # Distinct sightings on different lines get distinct offsets.
    assert kingfisher.byte_offset(source, 2, 0) != kingfisher.byte_offset(source, 3, 0)
    # Out-of-range / missing line -> None (no false collision at 0).
    assert kingfisher.byte_offset(source, 99, 0) is None
    assert kingfisher.byte_offset(source, None, None) is None


def test_locate_snippet_finds_exact_bytes_by_content():
    # The secret sits on line 3; an engine line/column pointing elsewhere is
    # irrelevant — locate_snippet finds the snippet by content, so the slice always
    # round-trips (this is the AWS-rule failure mode: match region != snippet line).
    source = 'const ID = "AKIAxxxxxxxxxxxxxxxx";\n// noise\nconst KEY = "wJalrSECRETzz";\n'
    snippet = "wJalrSECRETzz"
    located = kingfisher.locate_snippet(source, snippet)
    assert located is not None
    start, end = located
    assert source.encode("utf-8")[start:end].decode("utf-8") == snippet


def test_locate_snippet_repeats_get_distinct_offsets_via_cursor():
    source = 'a = "TOKEN123";\nb = "TOKEN123";\n'
    first = kingfisher.locate_snippet(source, "TOKEN123")
    assert first is not None
    second = kingfisher.locate_snippet(source, "TOKEN123", search_from=first[1])
    assert second is not None and second[0] > first[0]  # a distinct second sighting
    # A cursor past the last sighting finds nothing (honest offset-less), not a re-collision.
    assert kingfisher.locate_snippet(source, "TOKEN123", search_from=10_000) is None


def test_locate_snippet_absent_or_empty_is_none():
    assert kingfisher.locate_snippet("nothing to see", "SECRET") is None
    assert kingfisher.locate_snippet("anything", "") is None


def test_scan_missing_binary_degrades_gracefully():
    result = kingfisher.scan(b"const x = 1;", bin_path="definitely-not-kingfisher-xyzzy")
    assert result.status == "unavailable"
    assert result.secrets == []


def test_scan_reraises_genuine_engine_error():
    # A real failure (here: the interpreter run as if it were kingfisher exits
    # non-zero) must NOT be swallowed as "no secrets" — it fails the stage.
    with pytest.raises(engines.EngineError):
        kingfisher.scan(b"const x = 1;", bin_path=sys.executable)


def test_scan_real_binary_detects_planted_secret(engines_required):
    # Contract test against the REAL Kingfisher binary (REQ-T4): a planted Stripe
    # key must still be detected. If an upstream output rename dropped rule.id or
    # finding.snippet, parse_findings would yield nothing and this fails — that is
    # the drift gate. Built from split literals so no secret-shaped token is
    # committed; kingfisher reassembles and detects it at runtime.
    token = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
    source = f'const stripeKey = "{token}";\n'.encode()

    result = kingfisher.scan(source)
    if result.status == "unavailable":  # binary not installed in this environment
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")
    assert result.status == "ok"
    assert any("stripe" in secret.rule_id for secret in result.secrets)


def test_akia_rule_file_ships_where_scan_expects():
    # Guards a source-tree regression (rename/delete). Wheel packaging is enforced
    # separately by pyproject's package-data; the integration lane (pytest inside
    # the built image) is what catches a wheel that dropped the data file.
    assert kingfisher._RULES_PATH.is_file(), kingfisher._RULES_PATH


def test_scan_real_binary_detects_standalone_aws_access_key_id(engines_required):
    # A lone AWS Access Key ID with NO paired secret. Kingfisher's built-in AWS rule
    # treats the id as a pairing identity and, under --no-validate, never emits it —
    # it only tallies it in the summary's findings_by_rule — so before the shipped
    # standalone rule this was silently missed. Split literals so no key-shaped token
    # is committed; kingfisher reassembles the contiguous token at runtime.
    token = "AKIA" + "2E4XZ7K9QW3RT8YV"
    source = f'const accessKeyId = "{token}";\n'.encode()

    result = kingfisher.scan(source)
    if result.status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")
    assert result.status == "ok"
    ids = {secret.rule_id for secret in result.secrets}
    assert "custom.aws.akia_standalone" in ids, ids
    akia = next(s for s in result.secrets if s.rule_id == "custom.aws.akia_standalone")
    assert akia.snippet == token
    # The pinned provider override resolves the identity to aws, not "custom".
    assert normalize.normalize_secret_value(akia.snippet, akia.rule_id).startswith("aws:")
