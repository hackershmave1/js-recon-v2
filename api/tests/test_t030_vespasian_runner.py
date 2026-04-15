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
