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

import contextlib
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

# Windows/test hosts lack SIGKILL; the Linux container (where the crawl runs) has it.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


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
    # perf_counter, not monotonic: some Windows builds back time.monotonic() with
    # GetTickCount64 (~15ms resolution), coarse enough that a tight poll loop can
    # spin past a zero-duration deadline without the clock ever ticking over.
    # perf_counter uses QueryPerformanceCounter (sub-microsecond) there and is the
    # POSIX high-resolution monotonic clock elsewhere, so the deadline check is
    # reliable on both lanes.
    deadline = time.perf_counter() + duration_seconds + kill_grace_seconds
    # Deliberate long-lived handle: `out` is owned by the Popen below (its stdout)
    # for the child's whole lifetime and read after it exits, so a `with` here would
    # close it too early — hence the SIM115 suppression.
    out = tempfile.TemporaryFile()  # noqa: SIM115
    proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.DEVNULL, start_new_session=True)
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
                    redis,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    job_id=job_id,
                    done=step,
                    total=0,
                )
                if time.perf_counter() > deadline:
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
        os.killpg(os.getpgid(proc.pid), _KILL_SIGNAL)
    except (ProcessLookupError, PermissionError, OSError) as exc:  # already gone
        log.warning("discover.kill_group_failed", error=str(exc))
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
