"""Runtime JS capture driver — drive the baked-in headless Chromium over the Chrome
DevTools Protocol (CDP) and return every script the page EXECUTES.

Mechanism (why VM-level, not network): ``Debugger.scriptParsed`` fires for every
script V8 parses in the page, and ``Debugger.getScriptSource`` returns the exact
source it parsed. That recovers inline ``<script>`` blocks, runtime-injected
scripts, and ``eval``/``new Function`` code that has NO network response at all —
the completeness win over the static ``recon.fetch`` path. Slice 1 attaches to the
PAGE target only; worker / service-worker capture (``Target.setAutoAttach{flatten}``)
and an interaction driver (autoscroll / click / route-enumeration) are follow-ups.

SSRF NOTE (load-bearing): the browser resolves the navigated host and loads its
subresources itself, with NO per-hop IP pin and NO per-hop scope re-validation —
the SAME residual as the opt-in headless katana crawl (see ``recon.discover.crawl``
module docstring), and a widening vs the default static crawl. Capture is therefore
DEFAULT-OFF (``RECON_ENABLE_CAPTURE_MODE``); the stage re-validates each captured
script's URL against scope before storing; OS/network egress isolation is the
deferred egress-proxy slice.

Process discipline mirrors ``recon.discover.harness``: Chromium runs in its own
process group and a wall-clock deadline ``killpg``s the whole tree (reaping the
renderer/zygote children a plain child-kill would orphan). Every wait loop — port
discovery, ws discovery, script collection, per-source fetch — routes through a
single throttled ``_Beater`` so ``on_progress`` fires at most once per
``heartbeat_interval_s`` NO MATTER which phase blocks: the worker renews its job
lease (no peer reclaim → no double browser launch) and observes pause/cancel even
during a slow cold start. Host-lane unit tests mock the websocket + ``Popen``; the
real-browser path runs in the integration lane.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from recon.observability import get_logger

log = get_logger("recon.capture.driver")

# Windows/test hosts lack SIGKILL; the Linux container (where capture runs) has it.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)
_RECV_TICK_SECONDS = 0.25  # recv poll granularity; bounds progress/interrupt latency


class CaptureError(Exception):
    """The capture browser could not be launched, reached, or driven."""


@dataclass(frozen=True)
class CapturedScript:
    """One script V8 parsed in the page context.

    ``url`` is the script's URL as CDP reported it — a real ``http(s)`` URL for an
    external ``<script src>``, the document URL for an inline block, or ``""`` for
    an anonymous injected/``eval``'d script (the completeness case). The stage maps
    this to a unique, content-stable ``run_asset`` URL and scope-filters it."""

    url: str
    source: bytes
    source_map_url: str | None
    sha256: str


@dataclass(frozen=True)
class CaptureResult:
    """The capture outcome. ``nav_error`` is the ``Page.navigate`` failure text
    (DNS/TLS/``ERR_*``) when the main navigation failed — the stage records the run
    as ``blocked`` (→ PARTIAL) rather than a false ``ok``/DONE with zero scripts."""

    scripts: list[CapturedScript]
    nav_error: str | None


class _Beater:
    """Throttle ``on_progress`` to at most once per ``interval_s``, so the job lease
    is renewed and pause/cancel observed no matter how tight a poll loop spins.
    ``on_progress`` may raise (the stage's cooperative interrupt) to abort."""

    def __init__(self, on_progress: Callable[[int], None], interval_s: float) -> None:
        self._on_progress = on_progress
        self._interval = interval_s
        self._last = 0.0

    def maybe(self, n_scripts: int = 0) -> None:
        if time.perf_counter() - self._last >= self._interval:
            self.force(n_scripts)

    def force(self, n_scripts: int = 0) -> None:
        self._on_progress(n_scripts)  # may raise (pause/cancel) — Chromium killed in finally
        self._last = time.perf_counter()


def _chromium_argv(chrome_path: str, user_data_dir: str) -> list[str]:
    # --headless=new: modern headless. --no-sandbox + --disable-dev-shm-usage: run
    # Chromium as root in a container with a small /dev/shm (the established posture,
    # same as recon.discover.katana's headless launch). --remote-debugging-port=0:
    # OS-assigned free port (no collisions across concurrent captures) written to
    # <user-data-dir>/DevToolsActivePort. --remote-allow-origins=*: accept the CDP
    # websocket upgrade regardless of Origin (Chromium >=111 gate).
    return [
        chrome_path,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=0",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "about:blank",
    ]


def capture_scripts(
    target_url: str,
    *,
    chrome_path: str,
    nav_timeout_s: float,
    idle_settle_s: float,
    session_budget_s: float,
    heartbeat_interval_s: float,
    max_scripts: int,
    max_script_bytes: int,
    on_progress: Callable[[int], None] = lambda _n: None,
) -> CaptureResult:
    """Launch headless Chromium, navigate to ``target_url``, and return the executed
    scripts (deduped by content SHA-256) plus any navigation error.

    ``on_progress(n_scripts)`` is invoked before launch, at most once per
    ``heartbeat_interval_s`` throughout (via ``_Beater``), and at end; it may raise
    (the stage's pause/cancel check) to abort — Chromium is always killed in
    ``finally``. Raises :class:`CaptureError` on launch / port / connect failure."""
    deadline = time.perf_counter() + session_budget_s
    beater = _Beater(on_progress, heartbeat_interval_s)
    beater.force(0)  # early interrupt check + first beat, before paying the launch cost
    user_data_dir = tempfile.mkdtemp(prefix="recon-capture-")
    try:
        proc = subprocess.Popen(
            _chromium_argv(chrome_path, user_data_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        shutil.rmtree(user_data_dir, ignore_errors=True)
        raise CaptureError(f"could not launch Chromium: {exc}") from exc
    try:
        port = _read_devtools_port(
            user_data_dir, deadline=min(deadline, _cap(nav_timeout_s)), beater=beater
        )
        ws_url = _page_ws_url(port, deadline=min(deadline, _cap(nav_timeout_s)), beater=beater)
        return _drive(
            ws_url,
            target_url,
            deadline=deadline,
            nav_timeout_s=nav_timeout_s,
            idle_settle_s=idle_settle_s,
            max_scripts=max_scripts,
            max_script_bytes=max_script_bytes,
            beater=beater,
        )
    finally:
        _kill_group(proc)
        shutil.rmtree(user_data_dir, ignore_errors=True)


def _cap(seconds: float) -> float:
    return time.perf_counter() + seconds


def _read_devtools_port(user_data_dir: str, *, deadline: float, beater: _Beater) -> int:
    """Poll ``<user-data-dir>/DevToolsActivePort`` (line 1 = the chosen port) until
    Chromium writes it or the deadline passes, heartbeating so a slow cold start can
    never lapse the job lease."""
    port_file = Path(user_data_dir) / "DevToolsActivePort"
    while time.perf_counter() < deadline:
        beater.maybe()
        with contextlib.suppress(OSError, ValueError):
            lines = port_file.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].strip():
                return int(lines[0].strip())
        time.sleep(0.05)
    raise CaptureError("Chromium did not expose a DevTools port before the deadline")


def _page_ws_url(port: int, *, deadline: float, beater: _Beater) -> str:
    """Resolve the first page target's websocket debugger URL from ``/json``."""
    endpoint = f"http://127.0.0.1:{port}/json"
    while time.perf_counter() < deadline:
        beater.maybe()
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as resp:  # noqa: S310 - fixed localhost
                targets = json.loads(resp.read())
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        for target in targets:
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                return str(target["webSocketDebuggerUrl"])
        time.sleep(0.05)
    raise CaptureError("Chromium exposed no page target before the deadline")


def _drive(
    ws_url: str,
    target_url: str,
    *,
    deadline: float,
    nav_timeout_s: float,
    idle_settle_s: float,
    max_scripts: int,
    max_script_bytes: int,
    beater: _Beater,
) -> CaptureResult:
    # max_size=None: getScriptSource for a large bundle exceeds the 1 MiB default and
    # would otherwise raise. ping_interval=None: we run our own recv loop; CDP's ws
    # doesn't need library keepalive pings (which can trip spurious closes).
    with connect(ws_url, max_size=None, ping_interval=None, open_timeout=10) as ws:
        state = _Session(ws)
        state.send("Debugger.enable")
        state.send("Page.enable")
        nav_id = state.send("Page.navigate", {"url": target_url})
        meta, nav_error = _collect_parsed(
            state,
            nav_id=nav_id,
            deadline=deadline,
            nav_timeout_s=nav_timeout_s,
            idle_settle_s=idle_settle_s,
            max_scripts=max_scripts,
            beater=beater,
        )
        scripts = _fetch_sources(
            state,
            meta,
            deadline=deadline,
            max_script_bytes=max_script_bytes,
            beater=beater,
        )
        return CaptureResult(scripts=scripts, nav_error=nav_error)


class _Session:
    """Minimal CDP request/notification helper over one sync websocket."""

    def __init__(self, ws: object) -> None:
        self._ws = ws
        self._id = 0

    def send(self, method: str, params: dict | None = None) -> int:
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        return self._id

    def recv(self, timeout: float) -> dict | None:
        # ConnectionClosed (browser gone) intentionally propagates to the caller; a
        # non-JSON/binary frame (CDP never sends one) is skipped, not misrouted as a
        # retryable failure.
        try:
            raw = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None


def _collect_parsed(
    state: _Session,
    *,
    nav_id: int,
    deadline: float,
    nav_timeout_s: float,
    idle_settle_s: float,
    max_scripts: int,
    beater: _Beater,
) -> tuple[dict[str, dict], str | None]:
    """Collect ``Debugger.scriptParsed`` events (and note any ``Page.navigate``
    failure) until the page settles (no new script for ``idle_settle_s``), the
    per-navigation cap ``nav_timeout_s`` elapses, the deadline passes, or
    ``max_scripts`` is reached."""
    meta: dict[str, dict] = {}
    nav_error: str | None = None
    loop_start = time.perf_counter()
    last_script = loop_start
    while True:
        now = time.perf_counter()
        beater.maybe(len(meta))
        if now > deadline or (now - loop_start) > nav_timeout_s:
            break
        if meta and (now - last_script) > idle_settle_s and (now - loop_start) > 1.0:
            break  # settled
        try:
            msg = state.recv(_RECV_TICK_SECONDS)
        except ConnectionClosed:
            break  # browser went away — return what parsed so far
        if msg is None:
            continue
        if msg.get("method") == "Debugger.scriptParsed":
            params = msg["params"]
            meta[params["scriptId"]] = {
                "url": params.get("url", "") or "",
                "sourceMapURL": params.get("sourceMapURL") or None,
            }
            last_script = time.perf_counter()
            if len(meta) >= max_scripts:
                break
        elif msg.get("id") == nav_id:
            # The Page.navigate ack — a hard navigation failure (DNS/TLS/ERR_*) sets
            # errorText (or returns a protocol error), which the stage maps to a
            # "blocked" run instead of a false "ok". No scripts will parse after a
            # hard failure, so stop waiting.
            if "error" in msg:
                nav_error = str(msg["error"].get("message", "navigate error"))
            elif (msg.get("result") or {}).get("errorText"):
                nav_error = str(msg["result"]["errorText"])
            if nav_error:
                break
    return meta, nav_error


def _fetch_sources(
    state: _Session,
    meta: dict[str, dict],
    *,
    deadline: float,
    max_script_bytes: int,
    beater: _Beater,
) -> list[CapturedScript]:
    """Pull each parsed script's source and dedupe by content SHA-256."""
    out: list[CapturedScript] = []
    seen: set[str] = set()
    for script_id, info in meta.items():
        beater.maybe(len(out))
        if time.perf_counter() > deadline:
            break
        result = _get_script_source(state, script_id, deadline=deadline, beater=beater)
        if result is None:
            continue
        source = result.get("scriptSource")
        if not source:
            continue
        raw = source.encode("utf-8")
        if len(raw) > max_script_bytes:
            # Truncate on a codepoint boundary so a >cap script never stores invalid
            # trailing UTF-8 (the sha — and thus the blob key — is of the stored bytes).
            raw = raw[:max_script_bytes].decode("utf-8", "ignore").encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out.append(
            CapturedScript(
                url=info["url"],
                source=raw,
                source_map_url=info["sourceMapURL"],
                sha256=digest,
            )
        )
    beater.force(len(out))
    return out


def _get_script_source(
    state: _Session, script_id: str, *, deadline: float, beater: _Beater
) -> dict | None:
    """Send getScriptSource and read until its matching response (dropping any
    interleaved events), heartbeating so even a slow single fetch can't lapse the
    lease. Returns the ``result`` dict, or ``None`` on error/timeout."""
    want = state.send("Debugger.getScriptSource", {"scriptId": script_id})
    while time.perf_counter() < deadline:
        beater.maybe()
        try:
            msg = state.recv(0.5)
        except ConnectionClosed:
            return None
        if msg is None:
            continue
        if msg.get("id") == want:
            return None if "error" in msg else msg.get("result")
    return None


def _kill_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), _KILL_SIGNAL)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        log.warning("capture.kill_group_failed", error=str(exc))
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
