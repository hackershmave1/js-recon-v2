# Vespasian Discovery Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Vespasian as a 4th discovery engine that runs Katana (for JS file collection) concurrently with a Vespasian scan (for OpenAPI 3.0 spec generation), storing the spec as a downloadable session artifact.

**Architecture:** When `discovery_engine = "vespasian"` is selected, the existing `_discover_with_katana()` runs alongside a new `_run_vespasian_scan()` via `asyncio.gather()`. Katana results feed the existing JS ingestion pipeline unchanged. Vespasian writes `openapi.yaml` to `storage/sessions/{id}/openapi.yaml`. `hasOpenApiSpec` is derived from file existence at list time — no DB schema change needed. A new `GET /api/sessions/{id}/openapi` endpoint streams the file.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, asyncio subprocess, pytest, pytest-asyncio — frontend: vanilla JS with Bootstrap 5 classes.

**Design spec:** `docs/superpowers/specs/2026-04-15-vespasian-discovery-engine-design.md`

**Key reference links:**
- Vespasian repo + CLI flags: https://github.com/praetorian-inc/vespasian
- Vespasian architecture (CLAUDE.md): https://raw.githubusercontent.com/praetorian-inc/vespasian/refs/heads/main/CLAUDE.md
- Katana repo: https://github.com/projectdiscovery/katana
- OpenAPI 3.0 spec: https://spec.openapis.org/oas/v3.0.3
- asyncio subprocess docs: https://docs.python.org/3/library/asyncio-subprocess.html
- FastAPI FileResponse: https://fastapi.tiangolo.com/advanced/custom-response/#fileresponse
- pytest-asyncio: https://pytest-asyncio.readthedocs.io/

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `api/app/services/recon_job_runner.py` | Add `vespasian_binary`, `vespasian_timeout_seconds` to `ReconRunnerOptions`; add `_run_vespasian_scan()`, `_discover_with_vespasian()`; update `_discover_target()` routing |
| Modify | `api/app/api/routes/recon.py` | Add `"vespasian"` to valid engines set; add binary check; pass `vespasian_binary` to `ReconRunnerOptions` |
| Modify | `api/app/api/routes/sessions.py` | Add `hasOpenApiSpec` field to `list_sessions` response; add `GET /api/sessions/{id}/openapi` download route |
| Modify | `api/app/templates/dashboard.html` | Add Vespasian `<option>` to discovery engine `<select>` |
| Modify | `api/app/static/dashboard.js` | Add OpenAPI download button + badge to session card render |
| Create | `api/tests/test_t030_vespasian_runner.py` | Unit tests for `_run_vespasian_scan()` and `_discover_with_vespasian()` |
| Create | `api/tests/test_t031_vespasian_api.py` | Integration tests for recon.py validation and sessions.py endpoints |

---

## Task 1: Extend ReconRunnerOptions and Wire Vespasian Engine

**Files:**
- Modify: `api/app/services/recon_job_runner.py:55-69` (dataclass fields)
- Modify: `api/app/services/recon_job_runner.py:142-155` (`_discover_target` routing)
- Modify: `api/app/api/routes/recon.py:322-368` (validation + options construction)
- Create: `api/tests/test_t030_vespasian_runner.py`

---

- [ ] **Step 1.1: Create the test file with an options defaults test**

Create `api/tests/test_t030_vespasian_runner.py`:

```python
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
```

- [ ] **Step 1.2: Run the test to confirm it fails (fields don't exist yet)**

```bash
cd api && uv run pytest tests/test_t030_vespasian_runner.py::TestReconRunnerOptionsDefaults -v
```

Expected: `FAILED` — `TypeError: ReconRunnerOptions.__init__() got an unexpected keyword argument 'vespasian_binary'`

- [ ] **Step 1.3: Add `vespasian_binary` and `vespasian_timeout_seconds` to `ReconRunnerOptions`**

In `api/app/services/recon_job_runner.py`, edit the dataclass (currently lines 55–69). Add two fields after `ingest_batch_size`:

```python
@dataclass
class ReconRunnerOptions:
    urls: list[str]
    session_id: str
    same_origin_only: bool = True
    max_assets: int = 300
    max_depth: int = 2
    discovery_engine: str = "headless"
    katana_binary: str = "katana"
    include_sourcemaps: bool = True
    perform_analysis: bool = True
    wait_after_load_ms: int = 2500
    timeout_seconds: int = 20
    max_response_bytes: int = 12 * 1024 * 1024
    ingest_batch_size: int = 5
    vespasian_binary: str = "vespasian"
    vespasian_timeout_seconds: int = 600
```

- [ ] **Step 1.4: Update `_discover_target` to recognise the vespasian engine**

In `api/app/services/recon_job_runner.py`, edit `_discover_target` (currently line 144). The guard and routing block become:

```python
async def _discover_target(self, target_url: str) -> None:
    engine = str(self.options.discovery_engine or "headless").strip().lower()
    if engine not in {"headless", "katana", "hybrid", "vespasian"}:
        engine = "headless"

    if engine in {"headless", "hybrid"}:
        headless_urls = await self._discover_with_headless(target_url)
        for url in headless_urls:
            self._register_candidate(url, target_url, "headless_response", 0)

    if engine in {"katana", "hybrid"}:
        katana_urls = await self._discover_with_katana(target_url)
        for url in katana_urls:
            self._register_candidate(url, target_url, "katana", 0)

    if engine == "vespasian":
        vespasian_urls = await self._discover_with_vespasian(target_url)
        for url in vespasian_urls:
            self._register_candidate(url, target_url, "katana", 0)
```

*(The rest of `_discover_target` — the page queue loop — remains unchanged.)*

- [ ] **Step 1.5: Update `recon.py` validation and `ReconRunnerOptions` construction**

In `api/app/api/routes/recon.py`, make three edits:

**Edit A** — line 322, expand the valid engine check:
```python
    discovery_engine = str(request.discoveryEngine or "headless").strip().lower()
    if discovery_engine not in {"headless", "katana", "hybrid", "vespasian"}:
        raise HTTPException(status_code=422, detail="Invalid discoveryEngine. Use headless, katana, hybrid, or vespasian")
```

**Edit B** — after line 329 (the existing katana binary check), add the vespasian binary check:
```python
    katana_binary = resolve_binary_path("katana", env_var="KATANA_BINARY")
    if discovery_engine == "katana" and not katana_binary:
        raise HTTPException(
            status_code=422,
            detail="Katana engine requested but katana binary is not available in the current API runtime. Install katana or use headless/hybrid.",
        )

    vespasian_binary = resolve_binary_path("vespasian", env_var="VESPASIAN_BINARY")
    if discovery_engine == "vespasian" and not vespasian_binary:
        raise HTTPException(
            status_code=422,
            detail=(
                "Vespasian engine requested but the vespasian binary is not available. "
                "Install from https://github.com/praetorian-inc/vespasian or set "
                "the VESPASIAN_BINARY environment variable."
            ),
        )
```

**Edit C** — in the `ReconRunnerOptions(...)` construction (around line 355), add the new fields:
```python
    options = ReconRunnerOptions(
        urls=validated_targets,
        session_id=session_id,
        same_origin_only=request.sameOriginOnly,
        max_assets=request.maxAssets,
        max_depth=request.maxDepth,
        discovery_engine=discovery_engine,
        katana_binary=katana_binary or "katana",
        include_sourcemaps=request.includeSourceMaps,
        perform_analysis=request.performAnalysis,
        wait_after_load_ms=request.waitAfterLoadMs,
        timeout_seconds=request.timeoutSeconds,
        max_response_bytes=request.maxResponseBytes,
        vespasian_binary=vespasian_binary or "vespasian",
    )
```

- [ ] **Step 1.6: Run the options defaults test — must pass**

```bash
cd api && uv run pytest tests/test_t030_vespasian_runner.py::TestReconRunnerOptionsDefaults -v
```

Expected: `3 passed`

- [ ] **Step 1.7: Commit**

```bash
git add api/app/services/recon_job_runner.py api/app/api/routes/recon.py api/tests/test_t030_vespasian_runner.py
git commit -m "feat(vespasian): extend ReconRunnerOptions and wire engine routing"
```

---

## Task 2: Implement `_run_vespasian_scan()`

**Files:**
- Modify: `api/app/services/recon_job_runner.py` (add new method)
- Modify: `api/tests/test_t030_vespasian_runner.py` (add test class)

This method runs `vespasian crawl` then `vespasian generate rest` in a temporary directory and copies the resulting `openapi.yaml` to the session storage path. Errors are surfaced as exceptions (the caller handles non-fatal behaviour).

The storage base is read from the `STORAGE_PATH` env var (defaulting to `"storage"`), matching the convention used in tests (`os.environ.setdefault("STORAGE_PATH", ...)`).

Reference: Vespasian CLI flags — https://github.com/praetorian-inc/vespasian  
Reference: asyncio.create_subprocess_exec — https://docs.python.org/3/library/asyncio-subprocess.html

---

- [ ] **Step 2.1: Add `TestRunVespasianScan` class to the test file**

Append to `api/tests/test_t030_vespasian_runner.py`:

```python
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
```

- [ ] **Step 2.2: Run the tests to confirm they all fail (method doesn't exist)**

```bash
cd api && uv run pytest tests/test_t030_vespasian_runner.py::TestRunVespasianScan -v
```

Expected: `6 ERRORS` — `AttributeError: 'ReconJobRunner' object has no attribute '_run_vespasian_scan'`

- [ ] **Step 2.3: Implement `_run_vespasian_scan()` in `recon_job_runner.py`**

Add this method to `ReconJobRunner` after `_discover_with_katana()` (around line 610). The method also needs `import tempfile` at the top of the file — add it to the existing import block.

First, add `import tempfile` to the imports at the top of `api/app/services/recon_job_runner.py`:

```python
import tempfile
```

Then add the method to the class:

```python
    async def _run_vespasian_scan(self, target_url: str) -> None:
        """
        Runs ``vespasian crawl`` then ``vespasian generate rest`` in a temporary
        working directory and copies the resulting OpenAPI YAML to the session's
        storage directory.

        This method raises RuntimeError on any failure so that the caller
        (_discover_with_vespasian) can catch it as a non-fatal error.

        Vespasian CLI reference: https://github.com/praetorian-inc/vespasian
        OpenAPI 3.0 output spec: https://spec.openapis.org/oas/v3.0.3
        asyncio subprocess: https://docs.python.org/3/library/asyncio-subprocess.html
        """
        import os

        binary = (self.options.vespasian_binary or "vespasian").strip()
        timeout = max(30, int(self.options.vespasian_timeout_seconds))
        depth = max(0, int(self.options.max_depth))
        session_id = self.options.session_id

        storage_base = Path(os.environ.get("STORAGE_PATH", "storage"))
        session_dir = storage_base / "sessions" / session_id
        openapi_dest = session_dir / "openapi.yaml"

        with tempfile.TemporaryDirectory(prefix=f"vespasian-{session_id[:8]}-") as tmpdir:
            capture_path = Path(tmpdir) / "capture.json"
            spec_path = Path(tmpdir) / "openapi.yaml"

            # ── Step 1: crawl ──────────────────────────────────────────────
            # vespasian crawl flags:
            #   --depth       maximum link-follow depth (int)
            #   --timeout     wall-clock budget as Go duration string (e.g. "600s")
            #   --scope       same-origin | same-domain
            #   -o            output path for capture.json
            crawl_cmd = [
                binary, "crawl", target_url,
                "--depth", str(depth),
                "--timeout", f"{timeout}s",
                "--scope", "same-origin",
                "-o", str(capture_path),
            ]
            logger.info("vespasian crawl: %s", " ".join(crawl_cmd))
            crawl_proc = await asyncio.create_subprocess_exec(
                *crawl_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, crawl_stderr = await asyncio.wait_for(
                    crawl_proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                crawl_proc.kill()
                raise RuntimeError(
                    f"Vespasian crawl timed out after {timeout}s for {target_url}"
                )

            if crawl_proc.returncode != 0 or not capture_path.exists():
                stderr_text = (crawl_stderr or b"").decode("utf-8", errors="replace")[:400]
                raise RuntimeError(
                    f"Vespasian crawl failed (exit {crawl_proc.returncode}): {stderr_text}"
                )

            # ── Step 2: generate OpenAPI 3.0 YAML ─────────────────────────
            # vespasian generate flags:
            #   <api-type>    positional — rest | wsdl | graphql
            #   <capture>     positional — path to capture.json produced by crawl
            #   -o            output path for the OpenAPI YAML
            gen_cmd = [
                binary, "generate", "rest",
                str(capture_path),
                "-o", str(spec_path),
            ]
            logger.info("vespasian generate: %s", " ".join(gen_cmd))
            gen_proc = await asyncio.create_subprocess_exec(
                *gen_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, gen_stderr = await asyncio.wait_for(
                    gen_proc.communicate(), timeout=120
                )
            except asyncio.TimeoutError:
                gen_proc.kill()
                raise RuntimeError("Vespasian generate timed out after 120s")

            if gen_proc.returncode != 0 or not spec_path.exists():
                stderr_text = (gen_stderr or b"").decode("utf-8", errors="replace")[:400]
                raise RuntimeError(
                    f"Vespasian generate failed (exit {gen_proc.returncode}): {stderr_text}"
                )

            session_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(spec_path, openapi_dest)
            logger.info("OpenAPI spec saved to %s", openapi_dest)
```

- [ ] **Step 2.4: Run the scan tests — all must pass**

```bash
cd api && uv run pytest tests/test_t030_vespasian_runner.py::TestRunVespasianScan -v
```

Expected: `6 passed`

- [ ] **Step 2.5: Commit**

```bash
git add api/app/services/recon_job_runner.py api/tests/test_t030_vespasian_runner.py
git commit -m "feat(vespasian): implement _run_vespasian_scan with crawl+generate pipeline"
```

---

## Task 3: Implement `_discover_with_vespasian()`

**Files:**
- Modify: `api/app/services/recon_job_runner.py` (add method)
- Modify: `api/tests/test_t030_vespasian_runner.py` (add test class)

This method runs Katana and `_run_vespasian_scan` concurrently via `asyncio.gather`. It returns Katana's JS URL set. A Vespasian failure is non-fatal: logged as WARNING, session continues with only Katana results.

---

- [ ] **Step 3.1: Add `TestDiscoverWithVespasian` to the test file**

Append to `api/tests/test_t030_vespasian_runner.py`:

```python
@pytest.mark.asyncio
class TestDiscoverWithVespasian:
    """Tests for ReconJobRunner._discover_with_vespasian().

    _discover_with_katana and _run_vespasian_scan are patched so no
    subprocess or network activity occurs.
    """

    async def test_returns_katana_urls(self):
        """Katana URLs are returned; vespasian result (None) is discarded."""
        runner = make_runner()
        expected_urls = {"https://example.com/app.js", "https://example.com/vendor.js"}

        with patch.object(runner, "_discover_with_katana",
                          new=AsyncMock(return_value=expected_urls)):
            with patch.object(runner, "_run_vespasian_scan",
                              new=AsyncMock(return_value=None)):
                result = await runner._discover_with_vespasian("https://example.com")

        assert result == expected_urls

    async def test_vespasian_failure_is_nonfatal(self):
        """If _run_vespasian_scan raises, the method still returns Katana URLs."""
        runner = make_runner()
        katana_urls = {"https://example.com/main.js"}

        with patch.object(runner, "_discover_with_katana",
                          new=AsyncMock(return_value=katana_urls)):
            with patch.object(runner, "_run_vespasian_scan",
                              new=AsyncMock(side_effect=RuntimeError("crawl failed"))):
                result = await runner._discover_with_vespasian("https://example.com")

        assert result == katana_urls

    async def test_katana_failure_returns_empty_set(self):
        """If Katana raises, an empty set is returned (Vespasian may still succeed)."""
        runner = make_runner()

        with patch.object(runner, "_discover_with_katana",
                          new=AsyncMock(side_effect=RuntimeError("katana gone"))):
            with patch.object(runner, "_run_vespasian_scan",
                              new=AsyncMock(return_value=None)):
                result = await runner._discover_with_vespasian("https://example.com")

        assert result == set()

    async def test_both_tasks_run_concurrently(self):
        """Both _discover_with_katana and _run_vespasian_scan are awaited."""
        runner = make_runner()
        katana_mock = AsyncMock(return_value=set())
        vespa_mock  = AsyncMock(return_value=None)

        with patch.object(runner, "_discover_with_katana", new=katana_mock):
            with patch.object(runner, "_run_vespasian_scan", new=vespa_mock):
                await runner._discover_with_vespasian("https://example.com")

        katana_mock.assert_awaited_once_with("https://example.com")
        vespa_mock.assert_awaited_once_with("https://example.com")
```

- [ ] **Step 3.2: Run to confirm failure**

```bash
cd api && uv run pytest tests/test_t030_vespasian_runner.py::TestDiscoverWithVespasian -v
```

Expected: `4 ERRORS` — `AttributeError: 'ReconJobRunner' object has no attribute '_discover_with_vespasian'`

- [ ] **Step 3.3: Implement `_discover_with_vespasian()` in `recon_job_runner.py`**

Add this method immediately before `_run_vespasian_scan`:

```python
    async def _discover_with_vespasian(self, target_url: str) -> set[str]:
        """
        Runs Katana (for JS file collection) and Vespasian (for OpenAPI generation)
        concurrently against the same target URL.

        Returns the JS URL set from Katana — these feed the existing ingestion
        pipeline. Vespasian's output (openapi.yaml) is a side-channel artifact
        written to storage/sessions/{id}/openapi.yaml.

        Vespasian failure is non-fatal: a WARNING is logged and the method
        returns whatever Katana found (which may be an empty set if Katana
        also failed).

        asyncio.gather docs: https://docs.python.org/3/library/asyncio-task.html#asyncio.gather
        """
        katana_task   = asyncio.create_task(self._discover_with_katana(target_url))
        vespasian_task = asyncio.create_task(self._run_vespasian_scan(target_url))

        results = await asyncio.gather(katana_task, vespasian_task, return_exceptions=True)

        katana_result   = results[0]
        vespasian_result = results[1]

        if isinstance(vespasian_result, Exception):
            logger.warning(
                "Vespasian scan failed for %s (non-fatal, session continues): %s",
                target_url,
                vespasian_result,
            )

        if isinstance(katana_result, Exception):
            logger.warning(
                "Katana discovery failed for %s in vespasian mode: %s",
                target_url,
                katana_result,
            )
            return set()

        return katana_result if isinstance(katana_result, set) else set()
```

- [ ] **Step 3.4: Run all runner tests — must all pass**

```bash
cd api && uv run pytest tests/test_t030_vespasian_runner.py -v
```

Expected: `13 passed`

- [ ] **Step 3.5: Commit**

```bash
git add api/app/services/recon_job_runner.py api/tests/test_t030_vespasian_runner.py
git commit -m "feat(vespasian): implement _discover_with_vespasian with non-fatal error handling"
```

---

## Task 4: `hasOpenApiSpec` in Session List + OpenAPI Download Endpoint

**Files:**
- Modify: `api/app/api/routes/sessions.py` (list response + new route)
- Create: `api/tests/test_t031_vespasian_api.py`

`hasOpenApiSpec` is a boolean derived from checking whether `{STORAGE_PATH}/sessions/{id}/openapi.yaml` exists on disk. No DB column is added. The download endpoint streams the file using FastAPI's `FileResponse`.

FastAPI FileResponse docs: https://fastapi.tiangolo.com/advanced/custom-response/#fileresponse

---

- [ ] **Step 4.1: Create `api/tests/test_t031_vespasian_api.py`**

```python
"""Integration tests for Vespasian API additions.

Tests the hasOpenApiSpec field in GET /api/sessions and the
GET /api/sessions/{id}/openapi download endpoint.

Requires DATABASE_URL environment variable (skipped otherwise).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

STORAGE_PATH = f"/tmp/js-extractor-vespasian-api-test-{uuid.uuid4()}"
os.environ["STORAGE_PATH"] = STORAGE_PATH

from app.main import app


class TestHasOpenApiSpec:
    """GET /api/sessions returns hasOpenApiSpec reflecting file presence."""

    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())
        # Create a minimal session by saving a file
        self._create_session()

    def _create_session(self):
        js_content = "var x = 1;"
        payload = {
            "metadata": {"sessionId": self.session_id},
            "files": [{
                "url": "https://example.com/app.js",
                "contentHash": f"t031-{self.session_id[:8]}",
                "sessionId": self.session_id,
                "capturedAt": "2026-04-15T00:00:00Z",
                "contentType": "application/javascript",
                "contentEncoding": "identity",
                "contentLength": len(js_content.encode()),
                "content": js_content,
                "dependencies": [],
            }],
        }
        resp = self.client.post("/api/save-files", json=payload)
        assert resp.status_code == 200

    def _spec_path(self) -> Path:
        return Path(STORAGE_PATH) / "sessions" / self.session_id / "openapi.yaml"

    def test_has_openapi_spec_false_when_no_file(self):
        sessions = self.client.get("/api/sessions").json()
        session = next((s for s in sessions if s["id"] == self.session_id), None)
        assert session is not None
        assert session["hasOpenApiSpec"] is False

    def test_has_openapi_spec_true_when_file_exists(self):
        path = self._spec_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("openapi: '3.0.0'\ninfo:\n  title: Test\n")

        sessions = self.client.get("/api/sessions").json()
        session = next((s for s in sessions if s["id"] == self.session_id), None)
        assert session is not None
        assert session["hasOpenApiSpec"] is True

        path.unlink()  # cleanup


class TestOpenApiDownloadEndpoint:
    """GET /api/sessions/{id}/openapi streams openapi.yaml or returns 404."""

    def setup_method(self):
        self.client = TestClient(app)
        self.session_id = str(uuid.uuid4())

    def _spec_path(self) -> Path:
        return Path(STORAGE_PATH) / "sessions" / self.session_id / "openapi.yaml"

    def test_returns_404_when_no_spec(self):
        resp = self.client.get(f"/api/sessions/{self.session_id}/openapi")
        assert resp.status_code == 404
        assert "No OpenAPI spec" in resp.json()["detail"]

    def test_returns_yaml_file_when_spec_exists(self):
        spec_content = "openapi: '3.0.0'\ninfo:\n  title: My API\n  version: '1.0'\npaths: {}\n"
        path = self._spec_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(spec_content)

        resp = self.client.get(f"/api/sessions/{self.session_id}/openapi")
        assert resp.status_code == 200
        assert resp.headers["content-type"] in {
            "application/yaml", "application/yaml; charset=utf-8",
            "text/yaml", "text/yaml; charset=utf-8",
        }
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert spec_content in resp.text

        path.unlink()  # cleanup

    def test_filename_includes_session_id_prefix(self):
        path = self._spec_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("openapi: '3.0.0'\n")

        resp = self.client.get(f"/api/sessions/{self.session_id}/openapi")
        disposition = resp.headers.get("content-disposition", "")
        assert self.session_id[:8] in disposition

        path.unlink()

    def test_invalid_session_id_returns_404(self):
        resp = self.client.get("/api/sessions/not-a-uuid/openapi")
        assert resp.status_code == 404
```

- [ ] **Step 4.2: Run tests to confirm failure**

```bash
cd api && uv run pytest tests/test_t031_vespasian_api.py -v
```

Expected failures:
- `TestHasOpenApiSpec::test_has_openapi_spec_*` — `KeyError: 'hasOpenApiSpec'`
- `TestOpenApiDownloadEndpoint::*` — `404` (route doesn't exist yet returns 404 from FastAPI, but wrong reason)

- [ ] **Step 4.3: Add `hasOpenApiSpec` to `list_sessions` in `sessions.py`**

In `api/app/api/routes/sessions.py`, the `list_sessions` function returns a list comprehension at lines 359–374. `Path` is already imported at line 2. Add `os` import at the top and update the response dict:

Add to the top-level imports (after existing imports):
```python
import os
```

Update the response dict in `list_sessions` (the `return [...]` block):
```python
    return [
        {
            "id": str(session.id),
            "name": session.name,
            "createdAt": session.created_at.isoformat(),
            "source": session.source,
            "version": session.version,
            "fileCount": int(file_count or 0),
            "analysisSummary": {
                "completed": int(analysis_completed or 0),
                "failed": int(analysis_failed or 0),
                "performed": (int(analysis_completed or 0) + int(analysis_failed or 0)) > 0,
            },
            "captureCoverage": get_latest_session_capture_coverage(str(session.id)),
            "hasOpenApiSpec": (
                Path(os.environ.get("STORAGE_PATH", "storage"))
                / "sessions"
                / str(session.id)
                / "openapi.yaml"
            ).exists(),
        }
        for session, file_count, analysis_completed, analysis_failed in rows
    ]
```

- [ ] **Step 4.4: Add the OpenAPI download route to `sessions.py`**

Add this route after `list_sessions` (after line 375, before `list_session_files`):

```python
@router.get("/api/sessions/{session_id}/openapi")
def download_session_openapi(session_id: str):
    """
    Stream the Vespasian-generated OpenAPI 3.0 YAML spec for a session.

    Returns 404 if no spec has been generated (engine was not vespasian,
    or vespasian failed during the crawl).

    OpenAPI 3.0 specification: https://spec.openapis.org/oas/v3.0.3
    FastAPI FileResponse: https://fastapi.tiangolo.com/advanced/custom-response/#fileresponse
    """
    from fastapi.responses import FileResponse

    spec_path = (
        Path(os.environ.get("STORAGE_PATH", "storage"))
        / "sessions"
        / session_id
        / "openapi.yaml"
    )
    if not spec_path.exists():
        raise HTTPException(status_code=404, detail="No OpenAPI spec for this session.")

    short_id = session_id[:8] if len(session_id) >= 8 else session_id
    return FileResponse(
        path=str(spec_path),
        media_type="application/yaml",
        filename=f"openapi-{short_id}.yaml",
    )
```

- [ ] **Step 4.5: Run all API tests — must pass**

```bash
cd api && uv run pytest tests/test_t031_vespasian_api.py -v
```

Expected: `7 passed`

- [ ] **Step 4.6: Run the full test suite to check for regressions**

```bash
cd api && uv run pytest tests/ -v
```

Expected: all previously-passing tests still pass.

- [ ] **Step 4.7: Commit**

```bash
git add api/app/api/routes/sessions.py api/tests/test_t031_vespasian_api.py
git commit -m "feat(vespasian): add hasOpenApiSpec to session list + OpenAPI download endpoint"
```

---

## Task 5: Frontend — Create Session Dropdown Option

**Files:**
- Modify: `api/app/templates/dashboard.html:593-597`

This is a one-line HTML change. No test framework covers the template directly — verify manually in the browser.

---

- [ ] **Step 5.1: Add the Vespasian option to the discovery engine `<select>`**

In `api/app/templates/dashboard.html`, find the `<select id="create-session-discovery-engine">` block (currently lines 593–597):

```html
                                <select id="create-session-discovery-engine" class="form-select">
                                    <option value="katana" selected>Katana</option>
                                    <option value="hybrid">Hybrid (Katana + Headless)</option>
                                    <option value="headless">Headless only</option>
                                </select>
```

Replace with:

```html
                                <select id="create-session-discovery-engine" class="form-select">
                                    <option value="katana" selected>Katana</option>
                                    <option value="hybrid">Hybrid (Katana + Headless)</option>
                                    <option value="headless">Headless only</option>
                                    <option value="vespasian">Vespasian (Katana + OpenAPI)</option>
                                </select>
```

- [ ] **Step 5.2: Update the modal title to be engine-neutral**

The modal title currently reads "Create Session and Start Katana Recon" (line 575). Update it to be engine-neutral:

Find:
```html
                    <i class="fas fa-rocket me-2"></i>Create Session and Start Katana Recon
```

Replace with:
```html
                    <i class="fas fa-rocket me-2"></i>Create New Session
```

- [ ] **Step 5.3: Manual browser verification**

Start the dev server:
```bash
cd api && uv run uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` and click "+ Create New Session". Confirm the Discovery Engine dropdown contains four options: Katana, Hybrid (Katana + Headless), Headless only, Vespasian (Katana + OpenAPI).

- [ ] **Step 5.4: Commit**

```bash
git add api/app/templates/dashboard.html
git commit -m "feat(vespasian): add Vespasian option to Create Session discovery engine dropdown"
```

---

## Task 6: Frontend — Session Card OpenAPI Button and Badge

**Files:**
- Modify: `api/app/static/dashboard.js:3185-3210` (session card template)

When `session.hasOpenApiSpec` is `true`, two UI elements appear:
1. **Badge** in the badge row: green "OpenAPI" badge (consistent with "Analysis performed" style)
2. **Button** in the button row: soft-styled download link that hits `GET /api/sessions/{id}/openapi`

The button uses an `<a>` tag styled as a button with the `download` attribute so the browser saves the file rather than navigating. The `btn-outline-secondary` class maps to the no-border soft fill style already in `dashboard.css`.

---

- [ ] **Step 6.1: Add the OpenAPI badge to the session card badge row**

In `api/app/static/dashboard.js`, find the badge row section inside the session card template (around line 3185–3192):

```javascript
                            <div>
                                <span class="badge bg-primary me-2">${Number(session.fileCount) || 0} files</span>
                                <span class="badge bg-secondary">${this.escapeHtml(this.formatDateTime(session.createdAt))}</span>
                                ${analysisStatusBadge}
                                ${captureCoverageBadges}
                                <span data-session-progress-id="${session.id}">${progressBadges}</span>
                                <span data-session-recon-id="${session.id}">${reconBadges}</span>
                            </div>
```

Replace with:

```javascript
                            <div>
                                <span class="badge bg-primary me-2">${Number(session.fileCount) || 0} files</span>
                                <span class="badge bg-secondary">${this.escapeHtml(this.formatDateTime(session.createdAt))}</span>
                                ${analysisStatusBadge}
                                ${captureCoverageBadges}
                                <span data-session-progress-id="${session.id}">${progressBadges}</span>
                                <span data-session-recon-id="${session.id}">${reconBadges}</span>
                                ${session.hasOpenApiSpec ? '<span class="badge bg-success me-2">OpenAPI</span>' : ''}
                            </div>
```

- [ ] **Step 6.2: Add the OpenAPI download button to the session card button row**

In the same session card template, find the buttons flex container (around lines 3194–3210):

```javascript
                        <div class="d-flex align-items-center flex-wrap gap-2" style="flex-shrink:0">
                            <button class="btn btn-success btn-sm" ...>Analyze All</button>
                            <button class="btn btn-warning btn-sm ...">Stop</button>
                            <button class="btn btn-primary btn-sm" ...>Open Session</button>
                            <button class="btn btn-outline-primary btn-sm" ...>View Summary</button>
                            <button class="btn btn-outline-danger btn-sm" ...>Delete</button>
                        </div>
```

Add the OpenAPI button between "View Summary" and "Delete". Find the exact "View Summary" button line and add after it:

```javascript
                            ${session.hasOpenApiSpec ? `
                            <a class="btn btn-outline-secondary btn-sm"
                               href="/api/sessions/${session.id}/openapi"
                               download="openapi-${session.id.slice(0, 8)}.yaml"
                               title="Download OpenAPI 3.0 spec">
                                <i class="fas fa-file-code me-1"></i>OpenAPI
                            </a>` : ''}
```

The full updated button block becomes:

```javascript
                        <div class="d-flex align-items-center flex-wrap gap-2" style="flex-shrink:0">
                            <button class="btn btn-success btn-sm" data-session-analyze-id="${session.id}" ${analysisBusy ? 'disabled' : ''} onclick="dashboard.analyzeSession('${session.id}')">
                                <i class="fas fa-bolt me-1"></i>${analysisBusy ? (stopping ? 'Stopping...' : 'Analyzing...') : 'Analyze All'}
                            </button>
                            <button class="btn btn-warning btn-sm ${analysisBusy ? '' : 'd-none'}" data-session-stop-id="${session.id}" ${stopping ? 'disabled' : ''} onclick="dashboard.stopSessionAnalysis('${session.id}')">
                                <i class="fas fa-stop me-1"></i>${stopping ? 'Stopping...' : 'Stop'}
                            </button>
                            <button class="btn btn-primary btn-sm" onclick="dashboard.openSessionFiles('${session.id}', '${encodedName}')">
                                <i class="fas fa-folder-open me-1"></i>Open Session
                            </button>
                            <button class="btn btn-outline-primary btn-sm" ${analysisPerformed ? '' : 'disabled'} onclick="dashboard.showSessionSummary('${session.id}', '${encodedName}')">
                                <i class="fas fa-list-check me-1"></i>View Summary
                            </button>
                            ${session.hasOpenApiSpec ? `
                            <a class="btn btn-outline-secondary btn-sm"
                               href="/api/sessions/${session.id}/openapi"
                               download="openapi-${session.id.slice(0, 8)}.yaml"
                               title="Download OpenAPI 3.0 spec">
                                <i class="fas fa-file-code me-1"></i>OpenAPI
                            </a>` : ''}
                            <button class="btn btn-outline-danger btn-sm" data-session-delete-id="${session.id}" ${rowBusy ? 'disabled' : ''} onclick="dashboard.deleteSession('${session.id}')">
                                <i class="fas fa-trash me-1"></i>Delete
                            </button>
                        </div>
```

- [ ] **Step 6.3: Manual browser verification**

With the dev server running:

1. Open `http://localhost:8000`
2. Confirm: a session **without** a spec shows no OpenAPI badge or button
3. Manually create a fake spec file:
   ```bash
   mkdir -p api/storage/sessions/<any-existing-session-id>
   echo "openapi: '3.0.0'" > api/storage/sessions/<any-existing-session-id>/openapi.yaml
   ```
4. Refresh the dashboard — confirm the green "OpenAPI" badge and the "OpenAPI" download button appear on that session card
5. Click the "OpenAPI" button — confirm the browser downloads `openapi-{short_id}.yaml`
6. Delete the test file: `rm api/storage/sessions/<session-id>/openapi.yaml`

- [ ] **Step 6.4: Commit**

```bash
git add api/app/static/dashboard.js
git commit -m "feat(vespasian): add OpenAPI badge and download button to session card"
```

---

## Task 7: End-to-End Smoke Test and Final Check

**Goal:** Verify the full pipeline works: Vespasian engine selected → 422 when binary missing → (with binary) session created with `source=recon_vespasian` → spec appears.

---

- [ ] **Step 7.1: Verify 422 error when vespasian binary is not found**

With the dev server running and vespasian NOT installed:

```bash
curl -s -X POST http://localhost:8000/api/recon/jobs/start \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "discoveryEngine": "vespasian",
    "maxAssets": 10,
    "maxDepth": 1
  }' | python3 -m json.tool
```

Expected response:
```json
{
    "detail": "Vespasian engine requested but the vespasian binary is not available. Install from https://github.com/praetorian-inc/vespasian or set the VESPASIAN_BINARY environment variable."
}
```
HTTP status: `422`

- [ ] **Step 7.2: Install vespasian**

```bash
# Requires Go 1.21+: https://go.dev/doc/install
go install github.com/praetorian-inc/vespasian/cmd/vespasian@latest
vespasian version
```

Or set the env var to point at a downloaded binary:
```bash
export VESPASIAN_BINARY=/path/to/vespasian
```

Vespasian requires Chrome/Chromium for its headless crawl. Install if not present:
```bash
# Ubuntu/Debian
sudo apt-get install -y chromium-browser
# macOS
brew install --cask chromium
```

- [ ] **Step 7.3: Run a live Vespasian session against a local test target**

The simplest test target is a local server. Use Python's built-in HTTP server:

```bash
# Terminal 1 — serve a minimal page with a fetch() call
mkdir /tmp/test-api-target
cat > /tmp/test-api-target/index.html <<'EOF'
<html><body>
<script>
fetch('/api/users').then(r => r.json());
fetch('/api/products', {method: 'POST', body: JSON.stringify({name:'test'})});
</script>
</body></html>
EOF
cd /tmp/test-api-target && python3 -m http.server 9999
```

```bash
# Terminal 2 — create a Vespasian session
curl -s -X POST http://localhost:8000/api/recon/jobs/start \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://localhost:9999",
    "sessionName": "vespasian-smoke-test",
    "discoveryEngine": "vespasian",
    "maxAssets": 50,
    "maxDepth": 1,
    "timeoutSeconds": 60
  }' | python3 -m json.tool
```

Note the `jobId` and `sessionId` from the response.

- [ ] **Step 7.4: Poll until the job completes**

```bash
# Replace JOB_ID with the value from the previous step
curl -s http://localhost:8000/api/recon/jobs/JOB_ID | python3 -m json.tool
```

Poll until `"status"` is `"completed"` or `"failed"`.

- [ ] **Step 7.5: Verify `hasOpenApiSpec` and download the spec**

```bash
# Replace SESSION_ID with the session ID from step 7.3
curl -s http://localhost:8000/api/sessions | python3 -m json.tool | grep -A3 "vespasian-smoke-test"
```

Expected: `"hasOpenApiSpec": true` (if vespasian successfully crawled and generated).

```bash
curl -s http://localhost:8000/api/sessions/SESSION_ID/openapi
```

Expected: YAML output starting with `openapi: "3.0.0"`.

- [ ] **Step 7.6: Run the full backend test suite one final time**

```bash
cd api && uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7.7: Final commit**

```bash
git add -A
git commit -m "feat(vespasian): complete Vespasian discovery engine integration

Adds vespasian as a 4th session discovery engine. Katana runs concurrently
with vespasian scan to generate an OpenAPI 3.0 YAML spec stored as a session
artifact. Session card shows green OpenAPI badge and download button when
spec is available. All errors from vespasian are non-fatal.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All 7 touch points from the design spec are covered (recon.py, recon_job_runner.py, sessions.py, dashboard.html, dashboard.js, storage, new download route)
- [x] **No placeholders:** All code blocks contain working implementation, not "add validation here" stubs
- [x] **Type consistency:** `_discover_with_vespasian(target_url: str) -> set[str]` matches `_discover_with_katana`'s signature throughout
- [x] **`_run_vespasian_scan(target_url: str) -> None` matches how it's called in `_discover_with_vespasian`**
- [x] **`STORAGE_PATH` env var used consistently** in runner, sessions.py, and tests
- [x] **`asyncio.create_task` used** (not deprecated `ensure_future`)
- [x] **Binary check in recon.py covers both missing binary and wrong engine** (422 with install URL)
- [x] **Error path test coverage:** crawl failure, crawl timeout, generate failure, katana failure all tested
- [x] **Frontend `session.hasOpenApiSpec`** matches the key name returned by `list_sessions`
