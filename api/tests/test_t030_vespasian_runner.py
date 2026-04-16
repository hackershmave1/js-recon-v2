"""Unit tests for Vespasian recon runner integration.

Tests _run_vespasian_scan() and _discover_with_vespasian() in isolation
using asyncio subprocess mocks. No live Vespasian binary required.

References:
  asyncio.create_subprocess_exec: https://docs.python.org/3/library/asyncio-subprocess.html
  pytest-asyncio: https://pytest-asyncio.readthedocs.io/
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

os.environ.setdefault("STORAGE_PATH", f"/tmp/js-extractor-vespasian-test")

from app.services.recon_job_runner import ReconJobRunner, ReconRunnerOptions


def make_options(**overrides) -> ReconRunnerOptions:
    """Minimal ReconRunnerOptions for testing vespasian methods."""
    defaults = dict(
        urls=["https://example.com"],
        session_id="aaaaaaaa-bbbb-cccc-dddd-000000000001",
        discovery_engine="vespasian",
        vespasian_binary="vespasian",
        vespasian_timeout_seconds=30,
        max_depth=2,
    )
    defaults.update(overrides)
    return ReconRunnerOptions(**defaults)


def make_runner(options: ReconRunnerOptions | None = None) -> ReconJobRunner:
    """Construct a ReconJobRunner with a mocked DB session."""
    return ReconJobRunner(
        options=options or make_options(),
        db=MagicMock(),
    )


class TestReconRunnerOptionsDefaults:
    def test_vespasian_binary_default(self):
        opts = ReconRunnerOptions(urls=["https://x.com"], session_id="s1")
        assert opts.vespasian_binary == "vespasian"

    def test_vespasian_timeout_default(self):
        opts = ReconRunnerOptions(urls=["https://x.com"], session_id="s1")
        assert opts.vespasian_timeout_seconds == 600

    def test_vespasian_binary_override(self):
        opts = make_options(vespasian_binary="/usr/local/bin/vespasian")
        assert opts.vespasian_binary == "/usr/local/bin/vespasian"


def make_mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    """Return a mock async subprocess compatible with asyncio.create_subprocess_exec."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
class TestRunVespasianScan:
    """Tests for ReconJobRunner._run_vespasian_scan().

    Mocks asyncio.create_subprocess_exec and tempfile.TemporaryDirectory so
    no real vespasian binary or network access is needed.
    """

    def _setup_fake_tmpdir(self, tmp_path: Path) -> tuple[MagicMock, Path]:
        """
        Returns a mock TemporaryDirectory context manager and the work path.
        Also pre-creates the files that vespasian would write so the
        implementation's file-existence checks and shutil.copy succeed.
        """
        work = tmp_path / "vwork"
        work.mkdir()
        (work / "capture.json").write_text("[]")
        (work / "openapi.yaml").write_text("openapi: '3.0.0'\ninfo:\n  title: Test API\n")

        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=str(work))
        cm.__exit__ = MagicMock(return_value=False)
        return cm, work

    async def test_success_copies_spec_to_session_storage(self, tmp_path: Path):
        """Happy path: both subprocesses succeed → openapi.yaml lands in session dir."""
        session_id = "aaaaaaaa-bbbb-cccc-dddd-000000000001"
        options = make_options(
            session_id=session_id,
            vespasian_binary="vespasian",
            vespasian_timeout_seconds=30,
            max_depth=2,
        )
        runner = make_runner(options)

        cm, work = self._setup_fake_tmpdir(tmp_path)

        crawl_proc = make_mock_proc(returncode=0)
        gen_proc   = make_mock_proc(returncode=0)
        procs = iter([crawl_proc, gen_proc])

        with patch.dict("os.environ", {"STORAGE_PATH": str(tmp_path)}):
            with patch("tempfile.TemporaryDirectory", return_value=cm):
                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock,
                           side_effect=lambda *a, **k: next(procs)):
                    await runner._run_vespasian_scan("https://example.com")

        dest = tmp_path / "sessions" / session_id / "openapi.yaml"
        assert dest.exists(), "openapi.yaml was not written to session storage"
        assert "openapi: '3.0.0'" in dest.read_text()

    async def test_correct_crawl_command_built(self, tmp_path: Path):
        """Verifies the crawl subprocess is called with the right flags."""
        session_id = "aaaaaaaa-bbbb-cccc-dddd-000000000002"
        options = make_options(
            session_id=session_id,
            vespasian_binary="/usr/local/bin/vespasian",
            vespasian_timeout_seconds=60,
            max_depth=3,
        )
        runner = make_runner(options)
        cm, _ = self._setup_fake_tmpdir(tmp_path)

        crawl_proc = make_mock_proc(returncode=0)
        gen_proc   = make_mock_proc(returncode=0)
        procs = iter([crawl_proc, gen_proc])

        exec_calls: list[tuple] = []

        async def capture_exec(*args, **kwargs):
            exec_calls.append(args)
            return next(procs)

        with patch.dict("os.environ", {"STORAGE_PATH": str(tmp_path)}):
            with patch("tempfile.TemporaryDirectory", return_value=cm):
                with patch("asyncio.create_subprocess_exec", side_effect=capture_exec):
                    await runner._run_vespasian_scan("https://example.com")

        crawl_args = exec_calls[0]
        assert crawl_args[0] == "/usr/local/bin/vespasian"
        assert crawl_args[1] == "crawl"
        assert crawl_args[2] == "https://example.com"
        assert "--depth" in crawl_args
        assert str(3) in crawl_args
        assert "--timeout" in crawl_args
        assert "--scope" in crawl_args
        assert "same-origin" in crawl_args

    async def test_correct_generate_command_built(self, tmp_path: Path):
        """Verifies the generate subprocess uses 'rest' and the capture file path."""
        session_id = "aaaaaaaa-bbbb-cccc-dddd-000000000003"
        options = make_options(session_id=session_id)
        runner = make_runner(options)
        cm, work = self._setup_fake_tmpdir(tmp_path)
        capture_file = str(work / "capture.json")

        crawl_proc = make_mock_proc(returncode=0)
        gen_proc   = make_mock_proc(returncode=0)
        procs = iter([crawl_proc, gen_proc])

        exec_calls: list[tuple] = []

        async def capture_exec(*args, **kwargs):
            exec_calls.append(args)
            return next(procs)

        with patch.dict("os.environ", {"STORAGE_PATH": str(tmp_path)}):
            with patch("tempfile.TemporaryDirectory", return_value=cm):
                with patch("asyncio.create_subprocess_exec", side_effect=capture_exec):
                    await runner._run_vespasian_scan("https://example.com")

        gen_args = exec_calls[1]
        assert gen_args[0] == "vespasian"
        assert gen_args[1] == "generate"
        assert gen_args[2] == "rest"
        assert capture_file in gen_args

    async def test_crawl_nonzero_exit_raises(self, tmp_path: Path):
        """Non-zero crawl exit → RuntimeError raised (caller handles non-fatal)."""
        runner = make_runner()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=str(tmp_path / "w"))
        cm.__exit__ = MagicMock(return_value=False)
        (tmp_path / "w").mkdir()

        crawl_proc = make_mock_proc(returncode=1, stderr=b"crawl failed")

        with patch.dict("os.environ", {"STORAGE_PATH": str(tmp_path)}):
            with patch("tempfile.TemporaryDirectory", return_value=cm):
                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock,
                           return_value=crawl_proc):
                    with pytest.raises(RuntimeError, match="crawl failed"):
                        await runner._run_vespasian_scan("https://example.com")

    async def test_crawl_timeout_raises(self, tmp_path: Path):
        """Crawl timeout → RuntimeError with 'timed out' message."""
        runner = make_runner(make_options(vespasian_timeout_seconds=1))
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=str(tmp_path / "w"))
        cm.__exit__ = MagicMock(return_value=False)
        (tmp_path / "w").mkdir()

        async def slow_communicate():
            await asyncio.sleep(10)
            return (b"", b"")

        crawl_proc = MagicMock()
        crawl_proc.returncode = 0
        crawl_proc.communicate = slow_communicate
        crawl_proc.kill = MagicMock()

        with patch.dict("os.environ", {"STORAGE_PATH": str(tmp_path)}):
            with patch("tempfile.TemporaryDirectory", return_value=cm):
                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock,
                           return_value=crawl_proc):
                    with pytest.raises(RuntimeError, match="timed out"):
                        await runner._run_vespasian_scan("https://example.com")

    async def test_generate_nonzero_exit_raises(self, tmp_path: Path):
        """Non-zero generate exit → RuntimeError even when crawl succeeded."""
        session_id = "aaaaaaaa-bbbb-cccc-dddd-000000000004"
        options = make_options(session_id=session_id)
        runner = make_runner(options)
        cm, work = self._setup_fake_tmpdir(tmp_path)
        # Remove the openapi.yaml so generate appears to have produced nothing
        (work / "openapi.yaml").unlink()

        crawl_proc = make_mock_proc(returncode=0)
        gen_proc   = make_mock_proc(returncode=1, stderr=b"generate failed")
        procs = iter([crawl_proc, gen_proc])

        with patch.dict("os.environ", {"STORAGE_PATH": str(tmp_path)}):
            with patch("tempfile.TemporaryDirectory", return_value=cm):
                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock,
                           side_effect=lambda *a, **k: next(procs)):
                    with pytest.raises(RuntimeError, match="generate failed"):
                        await runner._run_vespasian_scan("https://example.com")
