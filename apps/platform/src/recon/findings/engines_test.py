"""Unit tests for the out-of-process engine harness.

Uses the test interpreter (``sys.executable``) as a stand-in engine binary so
the harness contract can be exercised with no external tool installed.
"""

from __future__ import annotations

import shutil
import sys

import pytest

from recon.findings import engines

# The RLIMIT_AS bound (DEBT D37-L0) is applied via `prlimit` (Linux/util-linux only);
# gate the real-enforcement tests so they run in CI's Linux lanes + the container but
# skip on the Windows dev host, where the limit is a documented no-op.
_PRLIMIT = sys.platform == "linux" and shutil.which("prlimit") is not None


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_returns_stdout_on_success():
    result = engines.run_engine(_py("print('hello')"), timeout_s=10, max_output_bytes=1024)
    assert result.returncode == 0
    assert b"hello" in result.stdout


def test_missing_binary_raises_not_available():
    with pytest.raises(engines.EngineNotAvailable):
        engines.run_engine(
            ["definitely-not-a-real-binary-xyzzy"], timeout_s=10, max_output_bytes=1024
        )


def test_timeout_is_raised():
    with pytest.raises(engines.EngineTimeout):
        engines.run_engine(_py("import time; time.sleep(5)"), timeout_s=0.5, max_output_bytes=1024)


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


# ---- D37-L0: per-child memory bound (RLIMIT_AS via a prlimit wrapper) ----


def test_memory_limit_argv_none_is_unchanged():
    # No limit -> the SAME argv object, so run_engine can detect "not wrapped".
    argv = ["sourcemapper", "-url", "x"]
    assert engines._memory_limit_argv(argv, None) is argv


def test_memory_limit_argv_wraps_with_prlimit(monkeypatch):
    monkeypatch.setattr(engines.sys, "platform", "linux")
    argv = ["sourcemapper", "-url", "x"]
    assert engines._memory_limit_argv(argv, 2048) == [
        "prlimit",
        "--as=2048",  # BYTES, not ulimit -v's KiB
        "--",
        "sourcemapper",
        "-url",
        "x",
    ]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_memory_limit_argv_is_noop_off_linux(monkeypatch, platform):
    # Off-Linux dev (Windows OR macOS): the wrapper is skipped — prlimit is util-linux, so
    # invoking it on a non-Linux host would soft-break every recovery. Gated on == "linux",
    # NOT != "win32" (macOS is darwin, which != "win32"). The Linux container enforces it.
    monkeypatch.setattr(engines.sys, "platform", platform)
    argv = ["sourcemapper", "-url", "x"]
    assert engines._memory_limit_argv(argv, 2048) is argv


def test_memory_limit_argv_nonpositive_is_disabled(monkeypatch):
    # <=0 disables the bound (repo's "<=0 disables" convention) — never `prlimit --as=0`,
    # which would trip every recovery. The container mem_limit remains the backstop.
    monkeypatch.setattr(engines.sys, "platform", "linux")
    argv = ["sourcemapper", "-url", "x"]
    assert engines._memory_limit_argv(argv, 0) is argv
    assert engines._memory_limit_argv(argv, -1) is argv


def test_missing_binary_under_memory_limit_still_not_available():
    # The prlimit wrapper would itself exec the engine, so without the pre-resolve guard a
    # MISSING binary would surface as prlimit's exit 127 (EngineError) and break the
    # EngineNotAvailable soft-skip contract. Platform-independent: on Linux the guard fires;
    # off-Linux the wrapper is a no-op and subprocess raises FileNotFoundError -> same class.
    with pytest.raises(engines.EngineNotAvailable):
        engines.run_engine(
            ["definitely-not-a-real-binary-xyzzy"],
            timeout_s=10,
            max_output_bytes=1024,
            memory_limit_bytes=256 * 1024 * 1024,
        )


@pytest.mark.skipif(not _PRLIMIT, reason="prlimit RLIMIT_AS enforcement is Linux/util-linux only")
def test_memory_limit_kills_over_size_child():
    # A child that allocates 1 GiB under a 256 MiB address-space cap dies (MemoryError ->
    # non-zero exit) -> EngineError, contained, never an OOM of this process.
    with pytest.raises(engines.EngineError):
        engines.run_engine(
            _py("bytearray(1024 * 1024 * 1024)"),
            timeout_s=30,
            max_output_bytes=1024,
            memory_limit_bytes=256 * 1024 * 1024,
        )


@pytest.mark.skipif(not _PRLIMIT, reason="prlimit RLIMIT_AS enforcement is Linux/util-linux only")
def test_memory_limit_allows_within_budget_child():
    # Regression guard: a child comfortably under the cap still runs (the limit must not
    # false-trip legitimate work — the whole point of measuring the value at real map sizes).
    result = engines.run_engine(
        _py("print('ok')"),
        timeout_s=30,
        max_output_bytes=1024,
        memory_limit_bytes=256 * 1024 * 1024,
    )
    assert result.returncode == 0
    assert b"ok" in result.stdout
