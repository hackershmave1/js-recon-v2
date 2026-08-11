"""Runtime JS capture driver — drive the baked-in headless Chromium over the Chrome
DevTools Protocol (CDP) and return every script the page tree EXECUTES.

Mechanism (why VM-level, not network): ``Debugger.scriptParsed`` fires for every
script V8 parses in a context, and ``Debugger.getScriptSource`` returns the exact
source it parsed. That recovers inline ``<script>`` blocks, runtime-injected
scripts, and ``eval``/``new Function`` code that has NO network response at all —
the completeness win over the static ``recon.fetch`` path.

Slice 2 captures the WHOLE execution tree, not just the page: it connects to the
BROWSER target and uses ``Target.setAutoAttach{flatten:true}`` — waterfalled onto
every attached session — to reach the page (via its ``tab`` parent), its dedicated /
shared workers (C7), and service workers (C8, which attach at the browser level, not
under the page). Every new target starts paused (``waitForDebuggerOnStart``); we
``Debugger.enable`` its session and only THEN ``Runtime.runIfWaitingForDebugger`` —
enabling first is what guarantees we see the target's very first ``scriptParsed``
(the release-order footgun). See ``recon.capture.cdp`` for the transport rationale
(one global id counter; responses matched by bare id because Chrome may omit
``sessionId`` on a reply).

SSRF NOTE (load-bearing): the browser resolves the navigated host and loads its
subresources itself, with NO per-hop IP pin and NO per-hop scope re-validation —
the SAME residual as the opt-in headless katana crawl (see ``recon.discover.crawl``
module docstring), and a widening vs the default static crawl. Workers / service
workers add no new egress surface — they are more of the same origin's execution
under the same gate. Capture is therefore DEFAULT-OFF (``RECON_ENABLE_CAPTURE_MODE``);
the stage re-validates each captured script's URL against scope before storing;
OS/network egress isolation is the deferred egress-proxy slice.

Process discipline mirrors ``recon.discover.harness``: Chromium runs in its own
process group and a wall-clock deadline ``killpg`` s the whole tree (reaping the
renderer / worker / zygote children a plain child-kill would orphan, regardless of
which CDP target we attached to). Every wait loop — port discovery, ws discovery,
tree collection, per-source fetch — routes through a single throttled ``_Beater`` so
``on_progress`` fires at most once per ``heartbeat_interval_s`` NO MATTER which phase
blocks: the worker renews its job lease (no peer reclaim → no double browser launch)
and observes pause/cancel even during a slow cold start. Host-lane unit tests mock the
websocket + ``Popen``; the real-browser path runs in the integration lane.
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

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from recon.capture import cdp
from recon.capture.cdp import CaptureError, CdpSession
from recon.observability import get_logger

log = get_logger("recon.capture.driver")

# Windows/test hosts lack SIGKILL; the Linux container (where capture runs) has it.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)
_RECV_TICK_SECONDS = 0.25  # recv poll granularity; bounds progress/interrupt latency
# Minimum drive time before "settled" can fire, so the multi-round-trip browser→tab→
# page→worker attach/navigate handshake is never mistaken for a quiet page. A module
# constant so host tests can shrink it (the real handshake needs the full second).
_MIN_DRIVE_SECONDS = 1.0
# Per-getScriptSource cap, min'd with the global deadline: a worker/SW that detaches
# mid-fetch may send NO reply at all, and without this bound one dead fetch would burn
# the entire remaining session budget and starve every later script.
_SCRIPT_FETCH_TIMEOUT_SECONDS = 8.0

__all__ = ["CaptureError", "CapturedScript", "CaptureResult", "capture_scripts"]


@dataclass(frozen=True)
class CapturedScript:
    """One script V8 parsed in some execution context.

    ``url`` is the script's URL as CDP reported it — a real ``http(s)`` URL for an
    external ``<script src>`` or a worker/SW entry, the document URL for an inline
    block, or ``""`` for an anonymous injected/``eval``'d script (the completeness
    case). ``target_type`` records which context parsed it (page / worker /
    service_worker / iframe) for provenance in logs; it is NOT part of the stored
    asset contract. The stage maps this to a unique, content-stable ``run_asset``
    URL and scope-filters it."""

    url: str
    source: bytes
    source_map_url: str | None
    sha256: str
    target_type: str


@dataclass(frozen=True)
class CaptureResult:
    """The capture outcome. ``nav_error`` is the ``Page.navigate`` failure text
    (DNS/TLS/``ERR_*``), or a sentinel when no page target ever attached — the stage
    records the run as ``blocked`` (→ PARTIAL) rather than a false ``ok``/DONE with
    zero scripts."""

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
    scripts across the whole target tree (deduped by content SHA-256) plus any
    navigation error.

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
        ws_url = _browser_ws_url(port, deadline=min(deadline, _cap(nav_timeout_s)), beater=beater)
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


def _browser_ws_url(port: int, *, deadline: float, beater: _Beater) -> str:
    """Resolve the BROWSER target's websocket debugger URL from ``/json/version``
    (not ``/json``, which lists page targets) — capture attaches at the browser level
    so ``Target.setAutoAttach`` can reach service workers, which are not page children."""
    endpoint = f"http://127.0.0.1:{port}/json/version"
    while time.perf_counter() < deadline:
        beater.maybe()
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as resp:  # noqa: S310 - fixed localhost
                info = json.loads(resp.read())
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        ws_url = info.get("webSocketDebuggerUrl")
        if ws_url:
            return str(ws_url)
        time.sleep(0.05)
    raise CaptureError("Chromium exposed no browser websocket before the deadline")


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
    try:
        conn = connect(ws_url, max_size=None, ping_interval=None, open_timeout=10)
    except (OSError, WebSocketException) as exc:
        # Surface as CaptureError so the stage maps it to a bounded retry explicitly
        # (matches the docstring), rather than leaking a raw OSError/handshake error.
        raise CaptureError(f"could not open the CDP websocket: {exc}") from exc
    with conn as ws:
        state = CdpSession(ws)
        meta, nav_error, detached = _collect_parsed(
            state,
            target_url=target_url,
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
            detached=detached,
            beater=beater,
        )
        return CaptureResult(scripts=scripts, nav_error=nav_error)


def _collect_parsed(
    state: CdpSession,
    *,
    target_url: str,
    deadline: float,
    nav_timeout_s: float,
    idle_settle_s: float,
    max_scripts: int,
    beater: _Beater,
) -> tuple[dict[tuple[str | None, str], dict], str | None, set[str]]:
    """Auto-attach the whole target tree, navigate the page, and collect every
    ``Debugger.scriptParsed`` (keyed by ``(sessionId, scriptId)``) until the tree
    settles (no new script for ``idle_settle_s``), the per-navigation cap
    ``nav_timeout_s`` elapses, the deadline passes, or ``max_scripts`` is reached.

    Returns the parsed-script metadata, any navigation error, and the set of sessions
    that detached mid-collection (their pending sources are skipped in the fetch pass)."""
    meta: dict[tuple[str | None, str], dict] = {}
    types: dict[str, str] = {}
    detached: set[str] = set()
    nav_error: str | None = None
    navigated = False
    nav_id: int | None = None
    page_session: str | None = None

    # Kick off the tree: auto-attach at the browser level with the ROOT filter (allows
    # `tab` + workers/SWs, excludes `page`; the waterfall reaches the page through its
    # tab — see cdp.ROOT_AUTO_ATTACH_PARAMS for why two filters are required).
    state.send("Target.setAutoAttach", cdp.ROOT_AUTO_ATTACH_PARAMS)

    loop_start = time.perf_counter()
    last_event = loop_start
    while True:
        now = time.perf_counter()
        beater.maybe(len(meta))
        if now > deadline or (now - loop_start) > nav_timeout_s:
            break
        # Settle only AFTER navigation and only once scripts have arrived, so the
        # multi-round-trip attach/navigate handshake never counts as "quiet".
        if (
            navigated
            and meta
            and (now - last_event) > idle_settle_s
            and (now - loop_start) > _MIN_DRIVE_SECONDS
        ):
            break
        try:
            msg = state.recv(_RECV_TICK_SECONDS)
        except ConnectionClosed:
            break  # browser went away — return what parsed so far
        if msg is None:
            continue

        method = msg.get("method")
        if method == "Target.attachedToTarget":
            nav_id, page_session, navigated = _on_attached(
                state,
                msg["params"],
                target_url=target_url,
                types=types,
                navigated=navigated,
                nav_id=nav_id,
                page_session=page_session,
            )
            last_event = time.perf_counter()
        elif method == "Debugger.scriptParsed":
            params = msg["params"]
            sid = msg.get("sessionId")
            meta[(sid, params["scriptId"])] = {
                "url": params.get("url", "") or "",
                "sourceMapURL": params.get("sourceMapURL") or None,
                "target_type": types.get(sid, "page"),
            }
            last_event = time.perf_counter()
            if len(meta) >= max_scripts:
                break
        elif method == "Target.detachedFromTarget":
            sid = msg["params"].get("sessionId")
            if sid:
                detached.add(sid)
        elif nav_id is not None and msg.get("id") == nav_id:
            # The Page.navigate ack — a hard navigation failure (DNS/TLS/ERR_*) sets
            # errorText (or returns a protocol error), which the stage maps to a
            # "blocked" run instead of a false "ok". Matched by bare id.
            if "error" in msg:
                nav_error = str(msg["error"].get("message", "navigate error"))
            elif (msg.get("result") or {}).get("errorText"):
                nav_error = str(msg["result"]["errorText"])
            if nav_error:
                break

    if not navigated and nav_error is None:
        # No page target ever attached (e.g. a filter/target-topology regression):
        # surface it as blocked rather than a silent "ok" with zero scripts.
        nav_error = "capture never attached to a page target"
    return meta, nav_error, detached


def _on_attached(
    state: CdpSession,
    params: dict,
    *,
    target_url: str,
    types: dict[str, str],
    navigated: bool,
    nav_id: int | None,
    page_session: str | None,
) -> tuple[int | None, str | None, bool]:
    """Handle one ``Target.attachedToTarget``: record its type, waterfall auto-attach
    onto it, enable Debugger (if debuggable), navigate the first page, and release it
    if it started paused — enabling BEFORE releasing so the first ``scriptParsed`` is
    never missed. Returns the (possibly updated) nav_id / page_session / navigated."""
    session_id = params["sessionId"]
    info = params.get("targetInfo", {})
    ttype = info.get("type", "")
    types[session_id] = ttype

    # Waterfall: setAutoAttach is per-session, so re-arm it (with the CHILD filter,
    # which allows `page`) on every child to reach the page under its `tab` parent and
    # any nested workers.
    state.send("Target.setAutoAttach", cdp.CHILD_AUTO_ATTACH_PARAMS, session_id=session_id)
    if ttype in cdp.DEBUGGABLE_TYPES:
        state.send("Debugger.enable", session_id=session_id)
    if ttype == "page" and not navigated:
        state.send("Page.enable", session_id=session_id)
        nav_id = state.send("Page.navigate", {"url": target_url}, session_id=session_id)
        page_session = session_id
        navigated = True
    if params.get("waitingForDebugger"):
        # Release AFTER enable — a released target can parse+GC before Debugger is on.
        state.send("Runtime.runIfWaitingForDebugger", session_id=session_id)
    return nav_id, page_session, navigated


def _fetch_sources(
    state: CdpSession,
    meta: dict[tuple[str | None, str], dict],
    *,
    deadline: float,
    max_script_bytes: int,
    detached: set[str],
    beater: _Beater,
) -> list[CapturedScript]:
    """Pull each parsed script's source from the session that parsed it and dedupe by
    content SHA-256. Scripts from a session that already detached are skipped (their
    source is gone)."""
    out: list[CapturedScript] = []
    seen: set[str] = set()
    for (session_id, script_id), info in meta.items():
        beater.maybe(len(out))
        if time.perf_counter() > deadline:
            break
        if session_id in detached:
            continue
        result = _get_script_source(
            state, session_id, script_id, deadline=deadline, beater=beater, n_done=len(out)
        )
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
                target_type=info["target_type"],
            )
        )
    beater.force(len(out))
    return out


def _get_script_source(
    state: CdpSession,
    session_id: str | None,
    script_id: str,
    *,
    deadline: float,
    beater: _Beater,
    n_done: int = 0,
) -> dict | None:
    """Send getScriptSource to the parsing session and read until its matching
    response (dropping any interleaved events), heartbeating throughout. Bounded by a
    per-call timeout min'd with the global deadline so a detached/silent session can't
    burn the whole budget. Matched by BARE id — Chrome may omit sessionId on the reply
    (see cdp module docstring). ``n_done`` (captured-so-far) is reported to the beat so
    progress doesn't flicker to 0 during a fetch. Returns the ``result`` dict, or
    ``None`` on error/timeout."""
    want = state.send("Debugger.getScriptSource", {"scriptId": script_id}, session_id=session_id)
    call_deadline = min(deadline, time.perf_counter() + _SCRIPT_FETCH_TIMEOUT_SECONDS)
    while time.perf_counter() < call_deadline:
        beater.maybe(n_done)
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
