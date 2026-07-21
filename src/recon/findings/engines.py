"""Out-of-process engine harness (P2 sandbox, MVP level).

External recon engines (Kingfisher today; Sourcemapper/katana later) run as
subprocesses, never in-process. This is the one place that spawns them, so the
safety controls live in one spot: a wall-clock timeout, an output-size cap, and
an explicit set of acceptable exit codes (many scanners use a non-zero code to
signal "findings found", not failure).

NOTE (follow-up, P2 sandbox hardening): this is MVP isolation only — timeout +
output cap + whatever the engine's own flags enforce (e.g. Kingfisher's
--no-validate/--no-update-check for "no network"). OS-level isolation (network
namespace / seccomp / nsjail, a read-only rootfs, cgroup memory limits) is
deferred. The process already runs as the non-root container user.

NOTE (subprocess limits): subprocess.run's timeout kills only the DIRECT child,
not a process group / grandchildren (would need start_new_session + os.killpg on
POSIX), and capture_output buffers stdout in RAM, so max_output_bytes bounds what
we *process*, not peak memory. Both are acceptable for Kingfisher — a
self-contained binary (threads, no child procs) fed an already size-capped input
(config.max_upload_bytes) — but a future streaming/hostile engine needs more.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


class EngineError(Exception):
    """The engine ran but failed (unexpected exit code, or output too large).

    Engine ``stderr`` is attached as an attribute, NOT folded into the message:
    the worker persists ``str(exc)`` into ``run.error``, the DLQ, and logs — none
    of them RLS-scoped — and a secret scanner can echo matched content to stderr
    on error. Callers read ``.stderr`` deliberately when they need the detail."""

    def __init__(self, message: str, *, stderr: bytes = b"") -> None:
        super().__init__(message)
        self.stderr = stderr


class EngineNotAvailable(EngineError):
    """The engine binary is not installed / not on PATH. Callers treat this as a
    soft absence (skip the engine), never a run failure."""


class EngineTimeout(EngineError):
    """The engine exceeded its wall-clock budget and was killed."""


@dataclass(frozen=True)
class EngineResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def resolve_bin(name: str) -> str:
    """Resolve an engine binary name to a runnable path.

    On PATH (the container, where the binary/console-script is installed) it is
    used as-is. In a non-activated venv (host dev/CI) PATH lacks the venv scripts
    dir, so we also look beside the running interpreter — where console scripts
    live — before giving up. An explicit path is used verbatim."""
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return name
    found = shutil.which(name)
    if found:
        return found
    scripts_dir = os.path.dirname(sys.executable)
    for candidate in (name, name + ".exe"):
        path = os.path.join(scripts_dir, candidate)
        if os.path.isfile(path):
            return path
    return name  # let subprocess raise FileNotFoundError -> EngineNotAvailable


def run_engine(
    argv: list[str],
    *,
    timeout_s: float,
    max_output_bytes: int,
    ok_returncodes: tuple[int, ...] = (0,),
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> EngineResult:
    """Run an external engine and return its captured output.

    Raises :class:`EngineNotAvailable` if the binary is missing,
    :class:`EngineTimeout` on timeout (the child is killed), and
    :class:`EngineError` on an unexpected exit code or oversized output.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built by us, never shell
            argv,
            capture_output=True,
            timeout=timeout_s,
            cwd=cwd,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EngineNotAvailable(argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and reaps the child before re-raising.
        raise EngineTimeout(f"{argv[0]} exceeded {timeout_s}s") from exc

    # Post-hoc cap: subprocess.run has already buffered stdout, so this bounds
    # what we *process* downstream, not what was received (a true streaming cap
    # would need Popen). Adequate because engine inputs are already size-capped.
    if len(completed.stdout) > max_output_bytes:
        raise EngineError(
            f"{argv[0]} produced {len(completed.stdout)} bytes (> {max_output_bytes})"
        )
    if completed.returncode not in ok_returncodes:
        # stderr goes on the exception attribute, never in the persisted message.
        raise EngineError(
            f"{argv[0]} exited {completed.returncode}", stderr=completed.stderr
        )

    return EngineResult(completed.returncode, completed.stdout, completed.stderr)
