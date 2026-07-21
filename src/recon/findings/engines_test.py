"""Unit tests for the out-of-process engine harness.

Uses the test interpreter (``sys.executable``) as a stand-in engine binary so
the harness contract can be exercised with no external tool installed.
"""

from __future__ import annotations

import sys

import pytest

from recon.findings import engines


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_returns_stdout_on_success():
    result = engines.run_engine(
        _py("print('hello')"), timeout_s=10, max_output_bytes=1024
    )
    assert result.returncode == 0
    assert b"hello" in result.stdout


def test_missing_binary_raises_not_available():
    with pytest.raises(engines.EngineNotAvailable):
        engines.run_engine(
            ["definitely-not-a-real-binary-xyzzy"], timeout_s=10, max_output_bytes=1024
        )


def test_timeout_is_raised():
    with pytest.raises(engines.EngineTimeout):
        engines.run_engine(
            _py("import time; time.sleep(5)"), timeout_s=0.5, max_output_bytes=1024
        )


def test_unexpected_exit_code_raises():
    with pytest.raises(engines.EngineError):
        engines.run_engine(_py("import sys; sys.exit(3)"), timeout_s=10, max_output_bytes=1024)


def test_exit_code_in_ok_set_is_accepted():
    # Mirrors Kingfisher's "200 = findings found" convention.
    result = engines.run_engine(
        _py("import sys; sys.exit(3)"),
        timeout_s=10,
        max_output_bytes=1024,
        ok_returncodes=(0, 3),
    )
    assert result.returncode == 3


def test_output_over_cap_raises():
    with pytest.raises(engines.EngineError):
        engines.run_engine(_py("print('x' * 500)"), timeout_s=10, max_output_bytes=10)
