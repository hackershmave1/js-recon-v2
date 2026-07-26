# Slice X — katana crawl discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `run.target = <bare domain>` into a real `DISCOVERING` stage that crawls the in-scope domain with headless katana and records an in-scope `.js` assets manifest, surfaced in the UI — fetch/analyze stay single-asset.

**Architecture:** A new out-of-process crawl in `recon/discover/`: `katana.py` builds the argv and parses katana's JSONL; a heartbeating `harness.py` runs katana via `Popen` in its own process group (poll + `progress.beat` renews the job lease so no peer worker reclaims and double-crawls; a wall-clock backstop `killpg`s the tree, reaping headless-Chrome grandchildren); `crawl.py` orchestrates the authorization/scope gate, egress re-validation of every emitted URL, the asset cap, the manifest blob, and a `discover.assets` event. A domain run then flows `DISCOVER (real) → FETCH no-op → ANALYZE no-op → DONE`, with the manifest as the deliverable.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Redis Streams / boto3 (MinIO/S3); katana (Go, headless via system chromium); React 19 / Vite / Vitest for the UI.

## Global Constraints

- Branch: `slice-discovery-katana` (spec `docs/superpowers/specs/2026-07-25-slice-discovery-katana-design.md`).
- Python package under `src/recon`; tests are **colocated** `*_test.py` (not a `tests/` dir).
- Host-lane test runner: `./.venv/Scripts/python.exe -m pytest <path> -v` (Windows dev host). Integration tests are marked `integration` and need `docker compose up -d` + `RECON_REQUIRE_ENGINES=1`.
- Front-end: `cd web && npm test -- --run <file>` (Vitest; does NOT type-check) and `cd web && npm run lint` (= `tsc -b --noEmit`). TS uses `erasableSyntaxOnly` + `verbatimModuleSyntax` — use `import type`; no TS parameter-property shorthand. `react-router` is v8.
- Conventional Commits, one isolated commit per task. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Secrets/hosts in committed tests must be inert (no real credentials).
- POSIX process-group calls (`os.killpg`, `start_new_session`) target the Linux container; host-lane unit tests exercise the harness with `subprocess.Popen` mocked, so they run on Windows.
- Discovery is **discovery-only**: katana enumerates JS asset URLs; parsing stays with Vespasian (do NOT use katana `-jc`). Multi-asset fetch/analyze, the occurrence schema migration, reveal routing, and `PARTIAL` completeness are **Slice Y — out of scope**.

---

### Task 1: Config + storage plumbing

**Files:**
- Modify: `src/recon/config.py` (add crawl settings)
- Modify: `src/recon/storage.py:24` (`BLOB_KINDS` += `"assets"`)
- Create: `src/recon/discover/__init__.py` (empty package marker)
- Test: `src/recon/discover/settings_test.py`

**Interfaces:**
- Produces: `get_settings().katana_bin/system_chrome_path/crawl_depth/crawl_duration_seconds/crawl_max_assets/crawl_max_output_bytes/crawl_heartbeat_interval_seconds/crawl_kill_grace_seconds`; `storage.object_key(tenant, run, "assets", bytes)` valid.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/discover/settings_test.py
from recon.config import get_settings
from recon import storage


def test_crawl_settings_have_defaults():
    s = get_settings()
    assert s.crawl_depth == 3
    assert s.crawl_max_assets == 500
    assert s.katana_bin == "katana"
    # Lease renewal invariant: a heartbeat must fire well within the stall window.
    assert s.crawl_heartbeat_interval_seconds < s.heartbeat_stall_threshold_seconds


def test_assets_is_a_valid_blob_kind():
    key = storage.object_key("t-1", "r-1", "assets", b"{}")
    assert key.split("/")[2] == "assets"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/settings_test.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'crawl_depth'` and `ValueError: unknown blob kind: 'assets'`.

- [ ] **Step 3: Add the settings**

In `src/recon/config.py`, after the fetch politeness block (`fetch_global_max_per_second`), add:

```python
    # Discovery/crawl stage (Slice X): headless katana crawl of an in-scope domain.
    # crawl_heartbeat_interval_seconds must stay well under
    # heartbeat_stall_threshold_seconds so the poll loop renews the job lease during
    # a long crawl and no peer worker reclaims the RUNNING job (double-crawl).
    katana_bin: str = "katana"
    system_chrome_path: str = "/usr/bin/chromium"
    crawl_depth: int = 3
    crawl_duration_seconds: float = 120.0
    crawl_max_assets: int = 500
    crawl_max_output_bytes: int = 32 * 1024 * 1024  # 32 MiB
    crawl_heartbeat_interval_seconds: float = 10.0
    crawl_kill_grace_seconds: float = 15.0
```

- [ ] **Step 4: Add the blob kind**

In `src/recon/storage.py`, change the `BLOB_KINDS` line to:

```python
BLOB_KINDS = frozenset({"input", "raw_js", "source_map", "reconstructed", "report", "assets"})
```

Create empty `src/recon/discover/__init__.py`.

- [ ] **Step 5: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/settings_test.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/recon/config.py src/recon/storage.py src/recon/discover/__init__.py src/recon/discover/settings_test.py
git commit -m "feat(discover): add crawl config settings + assets blob kind"
```

---

### Task 2: katana argv builder + JSONL parser

**Files:**
- Create: `src/recon/discover/katana.py`
- Test: `src/recon/discover/katana_test.py`

**Interfaces:**
- Produces: `build_argv(*, katana_bin, domain, scope_hosts, depth, crawl_duration_seconds, system_chrome_path) -> list[str]`; `parse_assets(stdout: bytes) -> list[str]` (ordered, de-duplicated http(s) `.js` URLs).

- [ ] **Step 1: Write the failing test**

```python
# src/recon/discover/katana_test.py
from recon.discover import katana


def test_build_argv_is_headless_scoped_jsonl():
    argv = katana.build_argv(
        katana_bin="katana", domain="acme.io", scope_hosts=["acme.io"],
        depth=3, crawl_duration_seconds=120.0, system_chrome_path="/usr/bin/chromium",
    )
    assert argv[0] == "katana"
    assert "-headless" in argv and "-no-sandbox" in argv
    assert "-jsonl" in argv
    assert argv[argv.index("-u") + 1] == "https://acme.io"
    assert argv[argv.index("-system-chrome-path") + 1] == "/usr/bin/chromium"
    assert "-em" in argv and argv[argv.index("-em") + 1] == "js"


def test_parse_assets_keeps_ordered_unique_js_urls():
    stdout = b"\n".join([
        b'{"request":{"endpoint":"https://acme.io/static/app.js"}}',
        b'not json - skipped',
        b'{"request":{"endpoint":"https://acme.io/vendor.js"}}',
        b'{"request":{"endpoint":"https://acme.io/static/app.js"}}',  # dup
        b'{"request":{"endpoint":"https://acme.io/index.html"}}',      # not .js
        b'{"endpoint":"https://acme.io/legacy.js"}',                    # top-level field
    ])
    assert katana.parse_assets(stdout) == [
        "https://acme.io/static/app.js",
        "https://acme.io/vendor.js",
        "https://acme.io/legacy.js",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/katana_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recon.discover.katana'`.

- [ ] **Step 3: Write the implementation**

```python
# src/recon/discover/katana.py
"""katana argv construction + JSONL parsing (discovery-only).

We drive katana purely as a JS-asset *discovery* crawler: it enumerates URLs,
and our own Vespasian parses them later — so we never pass ``-jc``. Flags drift
between katana releases; re-verify against the vendored version (``katana -h``)
and capture parse fixtures from real output. The JSON field carrying the crawled
URL is ``request.endpoint`` (top-level ``endpoint`` as a fallback); confirm
against the vendored katana's JSONL.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit


def build_argv(
    *,
    katana_bin: str,
    domain: str,
    scope_hosts: list[str],
    depth: int,
    crawl_duration_seconds: float,
    system_chrome_path: str,
) -> list[str]:
    target = domain if "://" in domain else f"https://{domain}"
    argv = [
        katana_bin, "-u", target,
        "-headless", "-no-sandbox",
        "-system-chrome", "-system-chrome-path", system_chrome_path,
        "-jsonl", "-silent", "-em", "js",
        "-depth", str(depth),
        "-crawl-duration", f"{crawl_duration_seconds:g}",
        "-field-scope", "rdn",
    ]
    for host in scope_hosts:
        argv += ["-crawl-scope", host]
    return argv


def parse_assets(stdout: bytes) -> list[str]:
    seen: dict[str, None] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = _extract_url(row)
        if url is None:
            continue
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            continue
        if not parts.path.lower().endswith(".js"):
            continue
        seen.setdefault(url, None)
    return list(seen)


def _extract_url(row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    request = row.get("request")
    if isinstance(request, dict) and isinstance(request.get("endpoint"), str):
        return request["endpoint"]
    endpoint = row.get("endpoint")
    return endpoint if isinstance(endpoint, str) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/katana_test.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/recon/discover/katana.py src/recon/discover/katana_test.py
git commit -m "feat(discover): katana argv builder + JSONL asset parser"
```

---

### Task 3: Heartbeating crawl harness

**Files:**
- Create: `src/recon/discover/harness.py`
- Test: `src/recon/discover/harness_test.py`

**Interfaces:**
- Consumes: `recon.progress.heartbeat.beat(redis, *, tenant_id, run_id, job_id, done, total)`.
- Produces: `run_crawl(redis, argv, *, tenant_id, run_id, job_id, duration_seconds, kill_grace_seconds, heartbeat_interval_seconds, max_output_bytes) -> CrawlResult`; `CrawlResult(stdout: bytes, timed_out: bool)`.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/discover/harness_test.py
import subprocess
from unittest.mock import MagicMock, patch

from recon.discover import harness


class _FakeProc:
    """A Popen stand-in: raises TimeoutExpired `stalls` times, then exits."""
    def __init__(self, stalls: int, output: bytes):
        self._stalls = stalls
        self._output = output
        self._exited = False
        self.pid = 4321

    def wait(self, timeout=None):
        if self._stalls > 0:
            self._stalls -= 1
            raise subprocess.TimeoutExpired(cmd="katana", timeout=timeout)
        self._exited = True
        return 0

    def poll(self):
        return 0 if self._exited else None


def test_run_crawl_beats_then_returns_output(tmp_path):
    proc = _FakeProc(stalls=2, output=b'{"request":{"endpoint":"https://acme.io/a.js"}}\n')
    tmpfile = tmp_path / "out"
    tmpfile.write_bytes(proc._output)
    beats = []
    redis = MagicMock()
    with patch("recon.discover.harness.subprocess.Popen", return_value=proc), \
         patch("recon.discover.harness.tempfile.TemporaryFile", return_value=open(tmpfile, "rb")), \
         patch("recon.discover.harness.progress.beat", side_effect=lambda *a, **k: beats.append(k)):
        result = harness.run_crawl(
            redis, ["katana"], tenant_id="t", run_id="r", job_id="j",
            duration_seconds=100.0, kill_grace_seconds=5.0,
            heartbeat_interval_seconds=0.01, max_output_bytes=1 << 20,
        )
    assert result.timed_out is False
    assert b"a.js" in result.stdout
    assert len(beats) == 2  # one per stall tick


def test_run_crawl_kills_group_on_backstop(tmp_path):
    proc = _FakeProc(stalls=1000, output=b"")   # never exits on its own
    tmpfile = tmp_path / "out"; tmpfile.write_bytes(b"")
    killed = []
    redis = MagicMock()
    with patch("recon.discover.harness.subprocess.Popen", return_value=proc), \
         patch("recon.discover.harness.tempfile.TemporaryFile", return_value=open(tmpfile, "rb")), \
         patch("recon.discover.harness.progress.beat"), \
         patch("recon.discover.harness.os.getpgid", return_value=4321), \
         patch("recon.discover.harness.os.killpg", side_effect=lambda *a: killed.append(a)):
        result = harness.run_crawl(
            redis, ["katana"], tenant_id="t", run_id="r", job_id="j",
            duration_seconds=0.0, kill_grace_seconds=0.0,
            heartbeat_interval_seconds=0.01, max_output_bytes=1 << 20,
        )
    assert result.timed_out is True
    assert killed  # os.killpg was called on the backstop
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/harness_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recon.discover.harness'`.

- [ ] **Step 3: Write the implementation**

```python
# src/recon/discover/harness.py
"""Heartbeating subprocess harness for the crawl (POSIX/container target).

katana runs far longer than the job lease and cannot heartbeat itself, so a
blocking ``subprocess.run`` would let a peer worker reclaim the RUNNING job and
launch a second headless crawl. Instead we ``Popen`` katana in its OWN process
group and poll: each tick beats (renewing the lease) so no reclaim happens, and
a wall-clock backstop ``killpg``s the whole tree — reaping headless-Chrome
grandchildren, which a plain child-kill would orphan. stdout is streamed to a
temp file (not a PIPE) so a chatty crawl can't deadlock on a full pipe buffer;
the size cap is applied on read. Host-lane unit tests mock ``Popen``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass

from redis import Redis

from recon.observability import get_logger
from recon.progress import heartbeat as progress

log = get_logger("recon.discover.harness")


@dataclass(frozen=True)
class CrawlResult:
    stdout: bytes
    timed_out: bool


def run_crawl(
    redis: Redis,
    argv: list[str],
    *,
    tenant_id: str,
    run_id: str,
    job_id: str,
    duration_seconds: float,
    kill_grace_seconds: float,
    heartbeat_interval_seconds: float,
    max_output_bytes: int,
) -> CrawlResult:
    deadline = time.monotonic() + duration_seconds + kill_grace_seconds
    out = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        argv, stdout=out, stderr=subprocess.DEVNULL, start_new_session=True
    )
    timed_out = False
    step = 0
    try:
        while True:
            try:
                proc.wait(timeout=heartbeat_interval_seconds)
                break  # katana exited on its own
            except subprocess.TimeoutExpired:
                step += 1
                progress.beat(
                    redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
                    done=step, total=0,
                )
                if time.monotonic() > deadline:
                    timed_out = True
                    _kill_group(proc)
                    break
    finally:
        if proc.poll() is None:
            _kill_group(proc)
        out.seek(0)
        stdout = out.read(max_output_bytes)
        out.close()
    return CrawlResult(stdout=stdout, timed_out=timed_out)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError) as exc:  # already gone
        log.warning("discover.kill_group_failed", error=str(exc))
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/harness_test.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/recon/discover/harness.py src/recon/discover/harness_test.py
git commit -m "feat(discover): heartbeating crawl harness with process-group kill"
```

---

### Task 4: Read queries (latest event + manifest load)

**Files:**
- Create: `src/recon/discover/queries.py`
- Test: `src/recon/discover/queries_test.py`

**Interfaces:**
- Produces: `latest_assets_event(tenant_id, run_id) -> dict | None` (payload of the newest `discover.assets` event); `get_assets_manifest(tenant_id, run_id) -> dict | None` (the manifest blob it points at).

- [ ] **Step 1: Write the failing test**

```python
# src/recon/discover/queries_test.py
import json

from recon import storage
from recon.db.base import tenant_session
from recon.events.log import record_event
from recon.discover import queries


def test_latest_assets_event_and_manifest(seeded_run):
    tenant_id, run_id = seeded_run
    manifest = {"domain": "acme.io", "status": "ok",
                "assets": [{"url": "https://acme.io/a.js", "source": "katana"}]}
    ref = storage.put_blob(tenant_id, run_id, "assets", json.dumps(manifest).encode())
    with tenant_session(tenant_id) as session:
        record_event(session, tenant_id=tenant_id, run_id=run_id,
                     event_type="discover.assets",
                     payload={"count": 1, "assets_ref": ref, "status": "ok"})
    assert queries.latest_assets_event(tenant_id, run_id)["count"] == 1
    assert queries.get_assets_manifest(tenant_id, run_id) == manifest


def test_manifest_none_when_no_event(seeded_run):
    tenant_id, run_id = seeded_run
    assert queries.get_assets_manifest(tenant_id, run_id) is None
```

Note: `seeded_run` is an existing fixture pattern in the findings/probe DB tests (creates a tenant + session + run and yields `(tenant_id, run_id)`). Reuse it via the package `conftest.py`; if the fixture is defined narrowly, lift it to `src/recon/conftest.py` in this task so `discover` tests can consume it. These are DB tests (need Postgres + MinIO) — run with the stack up.

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/queries_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recon.discover.queries'`.

- [ ] **Step 3: Write the implementation**

```python
# src/recon/discover/queries.py
"""Read side for discovery: the latest assets event + its manifest blob."""

from __future__ import annotations

import json

from sqlalchemy import select

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import RunEvent


def latest_assets_event(tenant_id: str, run_id: str) -> dict | None:
    with tenant_session(tenant_id) as session:
        row = session.execute(
            select(RunEvent.payload)
            .where(RunEvent.run_id == run_id, RunEvent.type == "discover.assets")
            .order_by(RunEvent.id.desc())
            .limit(1)
        ).first()
    return dict(row[0]) if row is not None else None


def get_assets_manifest(tenant_id: str, run_id: str) -> dict | None:
    payload = latest_assets_event(tenant_id, run_id)
    if payload is None or not payload.get("assets_ref"):
        return None
    return json.loads(storage.get_blob(payload["assets_ref"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/queries_test.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/recon/discover/queries.py src/recon/discover/queries_test.py src/recon/conftest.py
git commit -m "feat(discover): read queries for the assets manifest"
```

---

### Task 5: Discover orchestration (`discover_run`)

**Files:**
- Create: `src/recon/discover/crawl.py`
- Test: `src/recon/discover/crawl_test.py`

**Interfaces:**
- Consumes: `katana.build_argv`, `katana.parse_assets`, `harness.run_crawl`, `queries.latest_assets_event`, `egress.validate_target` / `egress.host_in_scope` / `egress.EgressBlocked`, `sessions.service.get_session`, `storage.put_blob`, `events.log.record_event` / `publish`, `retry.FatalError`.
- Produces: `discover_run(redis, *, tenant_id, run_id, job_id) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/discover/crawl_test.py
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from recon.discover import crawl
from recon.discover.harness import CrawlResult
from recon.fetch import egress
from recon.queue import retry


def _patches(katana_urls, validated, engagement, existing=None):
    """Common patch set: session lookup, harness, katana parse, egress, storage, event."""
    def validate(url, scope):
        if url not in validated:
            raise egress.EgressBlocked(f"blocked: {url}")
        return SimpleNamespace(url=url)
    return [
        # Mock the DB seam so these stay pure units (record_event is patched too).
        patch("recon.discover.crawl.tenant_session"),
        patch("recon.discover.crawl.queries.latest_assets_event", return_value=existing),
        patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1")),
        patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement),
        patch("recon.discover.crawl.harness.run_crawl",
              return_value=CrawlResult(stdout=b"", timed_out=False)),
        patch("recon.discover.crawl.katana.parse_assets", return_value=katana_urls),
        patch("recon.discover.crawl.egress.validate_target", side_effect=validate),
        patch("recon.discover.crawl.storage.put_blob", return_value="t/r/assets/deadbeef"),
    ]


def test_discover_run_writes_only_in_scope_assets():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    recorded = {}
    with patch("recon.discover.crawl.record_event",
               side_effect=lambda *a, **k: recorded.update(k) or MagicMock()), \
         patch("recon.discover.crawl.publish"):
        for p in _patches(
            katana_urls=["https://acme.io/app.js", "http://169.254.169.254/x.js"],
            validated={"https://acme.io/app.js"}, engagement=engagement,
        ):
            p.start()
        try:
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
        finally:
            patch.stopall()
    # The internal/out-of-scope URL was dropped by egress re-validation.
    assert recorded["payload"]["count"] == 1
    assert recorded["payload"]["status"] == "ok"


def test_discover_run_is_idempotent_when_event_exists():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    with patch("recon.discover.crawl.harness.run_crawl") as run_crawl, \
         patch("recon.discover.crawl.queries.latest_assets_event",
               return_value={"count": 3, "assets_ref": "x", "status": "ok"}):
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()  # no re-crawl on redelivery


def test_discover_run_rejects_unauthorized_session():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=False)
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1")), \
         patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement):
        with pytest.raises(retry.FatalError):
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/crawl_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recon.discover.crawl'`.

- [ ] **Step 3: Write the implementation**

```python
# src/recon/discover/crawl.py
"""Discover stage — crawl the run's domain into an in-scope .js assets manifest.

Idempotent: returns without re-crawling if a discover.assets event already exists
(a headless crawl must not repeat on redelivery). Every URL katana emits is
independently re-validated through the fetch stage's egress guard before it can
enter the manifest, so a scope-escape in katana output can never surface an
internal/out-of-scope URL (REQ-P2 / SSRF). The crawl's own subresource loads are
NOT guarded — accepted residual risk documented in the design spec.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from redis import Redis

from recon import storage
from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.discover import harness, katana, queries
from recon.events.log import publish, record_event
from recon.fetch import egress
from recon.observability import get_logger
from recon.queue import retry
from recon.sessions import service as sessions_service

log = get_logger("recon.discover")


def discover_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str) -> None:
    if queries.latest_assets_event(tenant_id, run_id) is not None:
        return  # already discovered (stage retry / redelivery)

    target, session_id = _load_target(tenant_id, run_id)
    if not target:
        return  # nothing to crawl (e.g. an upload run with no domain target)

    engagement = sessions_service.get_session(tenant_id, session_id)
    if engagement is None or not engagement.authorization_ack:
        raise retry.FatalError("session is not authorized for recon")
    if not egress.host_in_scope(_host(target), engagement.scope_hosts):
        raise retry.FatalError(f"crawl target not in engagement scope: {target}")

    settings = get_settings()
    argv = katana.build_argv(
        katana_bin=settings.katana_bin, domain=target,
        scope_hosts=engagement.scope_hosts, depth=settings.crawl_depth,
        crawl_duration_seconds=settings.crawl_duration_seconds,
        system_chrome_path=settings.system_chrome_path,
    )
    result = harness.run_crawl(
        redis, argv, tenant_id=tenant_id, run_id=run_id, job_id=job_id,
        duration_seconds=settings.crawl_duration_seconds,
        kill_grace_seconds=settings.crawl_kill_grace_seconds,
        heartbeat_interval_seconds=settings.crawl_heartbeat_interval_seconds,
        max_output_bytes=settings.crawl_max_output_bytes,
    )

    in_scope = _revalidate(katana.parse_assets(result.stdout), engagement.scope_hosts)
    capped = len(in_scope) > settings.crawl_max_assets
    kept = in_scope[: settings.crawl_max_assets]
    status = "timeout" if result.timed_out else ("capped" if capped else "ok")

    manifest = {
        "domain": target, "status": status,
        "assets": [{"url": u, "source": "katana"} for u in kept],
    }
    assets_ref = storage.put_blob(
        tenant_id, run_id, "assets", json.dumps(manifest).encode("utf-8")
    )
    with tenant_session(tenant_id) as session:
        event = record_event(
            session, tenant_id=tenant_id, run_id=run_id,
            event_type="discover.assets",
            payload={"count": len(kept), "assets_ref": assets_ref, "status": status},
        )
    publish(redis, event)
    log.info("discover.done", run_id=run_id, count=len(kept), status=status)


def _revalidate(urls: list[str], scope_hosts: list[str]) -> list[str]:
    kept: list[str] = []
    for url in urls:
        try:
            egress.validate_target(url, scope_hosts)
        except egress.EgressBlocked:
            continue
        kept.append(url)
    return kept


def _load_target(tenant_id: str, run_id: str) -> tuple[str | None, str | None]:
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None, None
        return run.target, str(run.session_id)


def _host(target: str) -> str:
    t = target if "://" in target else f"https://{target}"
    return urlsplit(t).hostname or ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/discover/crawl_test.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/recon/discover/crawl.py src/recon/discover/crawl_test.py
git commit -m "feat(discover): orchestrate crawl -> egress-revalidated assets manifest"
```

---

### Task 6: Worker wiring

**Files:**
- Modify: `src/recon/worker/main.py` (import + dispatch in `process_message`)
- Test: `src/recon/worker/main_discover_test.py`

**Interfaces:**
- Consumes: `crawl.discover_run(redis, *, tenant_id, run_id, job_id)`.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/worker/main_discover_test.py
from unittest.mock import MagicMock, patch

from recon.domain import RunStage
from recon.worker import main


def test_discovering_stage_calls_discover_run():
    called = {}
    with patch("recon.worker.main.crawl.discover_run",
               side_effect=lambda *a, **k: called.update(k)):
        main._run_stage_work(MagicMock(), RunStage.DISCOVERING,
                             tenant_id="t", run_id="r", job_id="j")
    assert called == {"tenant_id": "t", "run_id": "r", "job_id": "j"}
```

Note: this task introduces a tiny seam `_run_stage_work(redis, stage, *, tenant_id, run_id, job_id)` that holds the `if stage == …` dispatch, so the mapping is unit-testable without driving a whole message. Move the existing `FETCHING`/`ANALYZING` branches into it.

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/worker/main_discover_test.py -v`
Expected: FAIL — `AttributeError: module 'recon.worker.main' has no attribute '_run_stage_work'`.

- [ ] **Step 3: Write the implementation**

In `src/recon/worker/main.py`, add the import near the others:

```python
from recon.discover import crawl
```

Add the seam and route the real work through it. Replace the inline block (currently):

```python
            if stage == RunStage.FETCHING:
                fetch.fetch_run(redis, tenant_id=tenant_id, run_id=run_id)
            if stage == RunStage.ANALYZING:
                analyze.analyze_run(redis, tenant_id=tenant_id, run_id=run_id)
```

with a call:

```python
            _run_stage_work(redis, stage, tenant_id=tenant_id, run_id=run_id, job_id=job_id)
```

and define the seam near the top-level functions:

```python
def _run_stage_work(
    redis: Redis, stage: RunStage, *, tenant_id: str, run_id: str, job_id: str
) -> None:
    """Dispatch a stage to its real engine. Stubbed stages (INGEST/CORRELATE) are
    no-ops here; a bare-domain FETCH/ANALYZE also no-op (Slice X)."""
    if stage == RunStage.DISCOVERING:
        crawl.discover_run(redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id)
    elif stage == RunStage.FETCHING:
        fetch.fetch_run(redis, tenant_id=tenant_id, run_id=run_id)
    elif stage == RunStage.ANALYZING:
        analyze.analyze_run(redis, tenant_id=tenant_id, run_id=run_id)
```

Add `SERVED_QUEUES` already includes `DISCOVER` — no change there.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/worker/main_discover_test.py -v`
Then the existing worker tests: `./.venv/Scripts/python.exe -m pytest src/recon/worker -v`
Expected: PASS (new test + existing worker tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/recon/worker/main.py src/recon/worker/main_discover_test.py
git commit -m "feat(discover): wire the DISCOVERING stage to the crawl"
```

---

### Task 7: Read endpoint `GET /runs/{id}/assets`

**Files:**
- Modify: `src/recon/api/runs_router.py` (import + endpoint)
- Test: `src/recon/api/runs_router_assets_test.py`

**Interfaces:**
- Consumes: `discover.queries.get_assets_manifest`, `runs.queries.get_status`.
- Produces: `GET /runs/{run_id}/assets` → the manifest dict, or `{"domain": null, "status": "pending", "assets": []}` before discovery, or 404 for an unknown run.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/api/runs_router_assets_test.py
from fastapi.testclient import TestClient

from recon.api.app import app

client = TestClient(app)


def test_assets_pending_before_discovery(seeded_run):
    tenant_id, run_id = seeded_run
    res = client.get(f"/runs/{run_id}/assets", headers={"X-Tenant-Id": tenant_id})
    assert res.status_code == 200
    assert res.json() == {"domain": None, "status": "pending", "assets": []}


def test_assets_unknown_run_is_404(seeded_tenant):
    res = client.get("/runs/00000000-0000-0000-0000-000000000000/assets",
                     headers={"X-Tenant-Id": seeded_tenant})
    assert res.status_code == 404
```

Note: `seeded_run` / `seeded_tenant` are the existing API-test fixtures (see `sessions_router_test.py`); reuse them.

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/runs_router_assets_test.py -v`
Expected: FAIL — 404 route not found (endpoint missing).

- [ ] **Step 3: Write the implementation**

In `src/recon/api/runs_router.py`, add the import:

```python
from recon.discover import queries as discover_queries
```

Add the endpoint (near `get_status`):

```python
@router.get("/runs/{run_id}/assets")
def get_run_assets(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """The discovered in-scope .js assets manifest for a crawl run (REQ-C2).

    Returns a `pending` placeholder until the DISCOVERING stage records one."""
    if queries.get_status(tenant_id, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    manifest = discover_queries.get_assets_manifest(tenant_id, run_id)
    if manifest is None:
        return {"domain": None, "status": "pending", "assets": []}
    return manifest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/runs_router_assets_test.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/recon/api/runs_router.py src/recon/api/runs_router_assets_test.py
git commit -m "feat(discover): GET /runs/{id}/assets read endpoint"
```

---

### Task 8: Docker — install katana + chromium

**Files:**
- Modify: `Dockerfile`

**Interfaces:** produces `katana` on PATH and chromium at `/usr/bin/chromium` in the runtime image; no unit test (verified by image build + Task 10 integration).

- [ ] **Step 1: Add a katana build stage**

After the `sourcemapper-build` stage, add:

```dockerfile
# Build katana (JS-asset discovery crawler). Pin to a released tag, never @latest.
# Re-verify the tag + that `-headless -system-chrome` works against the vendored
# chromium at build time. See docs/slice2-deferred-debt.md (headless-Chrome/CGO note).
FROM golang:1.23-bookworm AS katana-build
RUN go install github.com/projectdiscovery/katana/cmd/katana@v1.1.0
```

- [ ] **Step 2: Install chromium + copy katana into the runtime image**

In the `python:3.11-slim-bookworm` stage, BEFORE the `useradd … USER app` lines, add:

```dockerfile
# Headless crawl needs a browser; katana drives system chromium over CDP.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

# katana binary onto PATH (root-owned, readable by the app user).
COPY --from=katana-build /go/bin/katana /usr/local/bin/katana
```

The config default `system_chrome_path = "/usr/bin/chromium"` matches the Debian chromium package; no env override needed. Headless Chrome as the non-root `app` user runs with `-no-sandbox` (already in the katana argv).

- [ ] **Step 3: Build to verify**

Run: `docker compose build api`
Expected: build succeeds; `docker compose run --rm api katana -version` prints a version and `docker compose run --rm api chromium --version` prints a Chromium version.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "build(discover): vendor katana + system chromium for headless crawl"
```

---

### Task 9: Front-end — crawl mode + assets inventory

**Files:**
- Modify: `web/src/api/types.ts` (add `AssetsManifest`)
- Modify: `web/src/api/apiClient.ts` (add `startRun`, `getAssets`)
- Modify: `web/src/features/newRun/NewRunPanel.tsx` (upload vs crawl mode)
- Create: `web/src/features/discovery/AssetsInventory.tsx`
- Modify: `web/src/app.tsx` (render `AssetsInventory` in the run workspace)
- Test: `web/src/api/apiClient.test.ts` (extend), `web/src/features/newRun/NewRunPanel.test.tsx` (extend), `web/src/features/discovery/AssetsInventory.test.tsx` (create)

**Interfaces:**
- Consumes: `POST /runs {session_id, target}` → `{run_id, state}`; `GET /runs/{id}/assets` → `AssetsManifest`.
- Produces: `startRun(tenantId, body) : Promise<RunRef>`; `getAssets(tenantId, runId) : Promise<AssetsManifest>`.

- [ ] **Step 1: Write the failing tests**

```tsx
// web/src/features/discovery/AssetsInventory.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssetsInventory } from "./AssetsInventory";
import * as api from "../../api/apiClient";

describe("AssetsInventory", () => {
  it("lists discovered assets with the crawl status", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue({
      domain: "acme.io", status: "ok",
      assets: [{ url: "https://acme.io/app.js", source: "katana" }],
    });
    render(<AssetsInventory tenantId="t" runId="r" />);
    await waitFor(() => expect(screen.getByText("https://acme.io/app.js")).toBeInTheDocument());
    expect(screen.getByText(/1 asset/i)).toBeInTheDocument();
    expect(screen.getByText(/ok/i)).toBeInTheDocument();
  });
});
```

Add to `web/src/api/apiClient.test.ts` a case asserting `startRun` POSTs `/runs` with the tenant header and JSON body, and `getAssets` GETs `/runs/{id}/assets` (follow the existing `uploadRun`/`getFindings` test patterns in that file).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npm test -- --run src/features/discovery/AssetsInventory.test.tsx`
Expected: FAIL — module `./AssetsInventory` not found.

- [ ] **Step 3: Add the types + client functions**

In `web/src/api/types.ts` add:

```ts
export interface AssetsManifest {
  domain: string | null;
  status: "pending" | "ok" | "capped" | "timeout";
  assets: { url: string; source: string }[];
}
```

In `web/src/api/apiClient.ts` add (and extend the top-of-file `import type` list with `AssetsManifest`):

```ts
export function startRun(
  tenantId: string, body: { session_id: string; target: string },
): Promise<RunRef> {
  return request("/runs", json("POST", body), tenantId);
}

export function getAssets(tenantId: string, runId: string): Promise<AssetsManifest> {
  return request(`/runs/${encodeURIComponent(runId)}/assets`, {}, tenantId);
}
```

- [ ] **Step 4: Build the component**

```tsx
// web/src/features/discovery/AssetsInventory.tsx
import { useEffect, useState } from "react";
import { getAssets } from "../../api/apiClient";
import type { AssetsManifest } from "../../api/types";

export function AssetsInventory({ tenantId, runId }: { tenantId: string; runId: string }) {
  const [manifest, setManifest] = useState<AssetsManifest | null>(null);

  useEffect(() => {
    let live = true;
    getAssets(tenantId, runId).then((m) => { if (live) setManifest(m); }).catch(() => {});
    return () => { live = false; };
  }, [tenantId, runId]);

  if (!manifest) return null;
  return (
    <section className="card">
      <h3>Discovered JavaScript</h3>
      <p className="muted">{manifest.assets.length} asset{manifest.assets.length === 1 ? "" : "s"} · crawl status: {manifest.status}</p>
      <ul>
        {manifest.assets.map((a) => (<li key={a.url}><code>{a.url}</code></li>))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 5: Add crawl mode to NewRunPanel + render the inventory**

In `web/src/features/newRun/NewRunPanel.tsx`, add a mode toggle. Import `startRun` alongside `uploadRun`, add `const [mode, setMode] = useState<"upload" | "crawl">("upload");` and `const [domain, setDomain] = useState("");`. In `submit`, branch:

```tsx
      if (mode === "crawl") {
        const run = await startRun(tenantId, { session_id: session.session_id, target: domain.trim() });
        navigate(`/runs/${run.run_id}`);
        return;
      }
```

Render two radio buttons (`upload` / `crawl`); in crawl mode show a domain `<input>` instead of the file input, and gate `ready` on `domain.trim() !== ""` for crawl. Change the submit label to `"Crawl"` in crawl mode. Keep the existing upload path unchanged.

In `web/src/app.tsx`, in the run-workspace route (where `FindingsView`/`RunProgress` render), add `<AssetsInventory tenantId={tenantId} runId={runId} />` above the findings section (import it from `./features/discovery/AssetsInventory`).

- [ ] **Step 6: Run tests + lint to verify they pass**

Run: `cd web && npm test -- --run src/features/discovery/AssetsInventory.test.tsx src/api/apiClient.test.ts src/features/newRun/NewRunPanel.test.tsx`
Then: `cd web && npm run lint`
Expected: PASS; lint clean.

- [ ] **Step 7: Commit**

```bash
git add web/src/api/types.ts web/src/api/apiClient.ts web/src/api/apiClient.test.ts web/src/features/newRun/NewRunPanel.tsx web/src/features/newRun/NewRunPanel.test.tsx web/src/features/discovery/AssetsInventory.tsx web/src/features/discovery/AssetsInventory.test.tsx web/src/app.tsx
git commit -m "feat(discover): crawl-a-domain mode + discovered-assets inventory UI"
```

---

### Task 10: Integration + security-regression + walkthrough

**Files:**
- Create: `docker-compose.yml` service `fixture-site` (a static site to crawl) OR reuse an existing test fixture host — see Step 1.
- Create: `web/fixtures/` (or `src/recon/discover/fixtures/`) minimal static site: `index.html` linking `app.js` + `vendor.js`, plus one out-of-scope `<script>` for the security assertion.
- Create: `src/recon/discover/crawl_integration_test.py`

**Interfaces:** none new. Verifies the real katana + chromium path end-to-end.

- [ ] **Step 1: Add a controlled fixture site**

Add a static-file service to `docker-compose.yml` on the compose network (so the worker can reach it in-scope), e.g. an `nginx:alpine` (or `python -m http.server`) serving `web/fixtures/`:

```yaml
  fixture-site:
    image: nginx:alpine
    volumes:
      - ./web/fixtures:/usr/share/nginx/html:ro
```

`web/fixtures/index.html` references `app.js`, `vendor.js`, and (for the security test) a `<script src="http://169.254.169.254/meta.js">`. The scope host declared for the run is the fixture-site host only.

- [ ] **Step 2: Write the integration + security test**

```python
# src/recon/discover/crawl_integration_test.py
import pytest

pytestmark = pytest.mark.integration  # real katana+chromium; needs docker + RECON_REQUIRE_ENGINES=1


def test_crawl_discovers_in_scope_js_and_drops_internal(discovery_run_over_fixture):
    """A real headless crawl of the fixture site yields app.js + vendor.js and
    NEVER the out-of-scope 169.254.x asset (egress re-validation)."""
    manifest = discovery_run_over_fixture  # fixture drives a full run to DONE, returns the manifest
    urls = {a["url"] for a in manifest["assets"]}
    assert any(u.endswith("/app.js") for u in urls)
    assert any(u.endswith("/vendor.js") for u in urls)
    assert not any("169.254.169.254" in u for u in urls)
    assert manifest["status"] in ("ok", "capped")
```

The `discovery_run_over_fixture` fixture (in `src/recon/discover/conftest.py`): creates a tenant + session scoped to the fixture-site host with `authorization_ack`, starts a run with `target=<fixture-site host>`, runs the worker until the run reaches `DONE`, and returns `discover.queries.get_assets_manifest(...)`.

- [ ] **Step 3: Run the integration test (stack up)**

Run: `docker compose up -d && RECON_REQUIRE_ENGINES=1 ./.venv/Scripts/python.exe -m pytest src/recon/discover/crawl_integration_test.py -m integration -v`
Expected: PASS — app.js + vendor.js discovered; the 169.254 asset absent.

- [ ] **Step 4: Full host-lane regression**

Run: `./.venv/Scripts/python.exe -m pytest -m "not integration" -q`
Then: `cd web && npm test -- --run && npm run lint`
Expected: all green (no regressions in existing suites).

- [ ] **Step 5: Live visual walkthrough (Mode A)**

With the Docker stack + built SPA (`docker compose build api && docker compose up -d`), open the UI, pick "crawl a domain", enter the fixture-site host, and confirm: the run streams through DISCOVERING, reaches DONE, and the "Discovered JavaScript" inventory lists app.js + vendor.js with status `ok`. Capture a screenshot for the PR. No console errors.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml web/fixtures src/recon/discover/crawl_integration_test.py src/recon/discover/conftest.py
git commit -m "test(discover): real-katana crawl integration + egress-drop regression + fixture site"
```

---

## Post-implementation gates (§4)

- **Gate 2 — higher-model code review:** hand the whole `slice-discovery-katana` diff to a higher-capability model subagent before merge. Confirm: the heartbeat interval genuinely renews the lease (no double-crawl), the process-group kill reaps Chrome, egress re-validation is applied to every emitted URL, idempotency holds on stage retry, and no Slice-Y concern leaked in.
- **Debt ledger:** append to `docs/slice2-deferred-debt.md` — egress hardening (deployment network control → egress proxy → netns/nftables); mid-crawl cancel/pause (currently takes effect at the next stage boundary); Slice Y (multi-asset fetch/analyze, occurrence asset-dimension migration, reveal routing, PARTIAL); OpenAPI export.

## Self-review notes

- **Spec coverage:** DISCOVERING stage (T5/T6), heartbeating harness + process-group kill (T3), assets manifest + BLOB_KINDS (T1/T5), egress re-validation (T5 + T10 security test), read endpoint + UI inventory (T7/T9), Docker chromium/katana (T8), config caps (T1), integration + walkthrough (T10). Idempotency (T5). All spec §3 in-scope items map to a task.
- **Out-of-scope guard:** no task touches `store.py`/`analyze.py` occurrence identity, `reveal.py`, or `coordinator.advance` completeness — those are Slice Y.
- **Type consistency:** `discover_run(redis, *, tenant_id, run_id, job_id)`, `run_crawl(...) -> CrawlResult(stdout, timed_out)`, `parse_assets(bytes) -> list[str]`, `get_assets_manifest -> dict | None`, `AssetsManifest{domain,status,assets[]}` used consistently across tasks and the FE.
