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

Slice 3 adds an INTERACTION driver (``recon.capture.interaction``): once the initial
load settles, it autoscrolls, clicks every interactive element, and walks same-origin
routes so lazily-loaded / route-split / click-gated chunks execute and get captured
by the same ``scriptParsed`` loop. Because interaction navigates the page multiple
times and a navigation destroys the prior document's V8 context (making
``getScriptSource`` for its scripts fail), the driver fetches each source EAGERLY —
the instant a script parses — via one unified event pump: a ``scriptParsed`` both
records metadata AND issues its ``getScriptSource`` immediately, and the reply is
matched later by bare id. That single loop routes attaches, parses, source replies,
navigation acks, and detaches without ever DROPPING an interleaved event (a
collect-then-fetch pass would strand every route but the last, and a naive
per-command wait would drop the parses it is meant to capture).

SSRF NOTE (load-bearing): the browser resolves the navigated host and loads its
subresources itself, with NO per-hop IP pin and NO per-hop scope re-validation — the
SAME residual as the opt-in headless katana crawl (see ``recon.discover.crawl``
module docstring). Capture is DEFAULT-OFF (``RECON_ENABLE_CAPTURE_MODE``); route-enum
navigates same-origin only and click-all does not follow off-origin links, and the
stage re-validates each captured script's URL against scope before storing. Enforcing
scope at the request layer (blocking off-scope egress before it is sent) and OS-level
egress isolation are the deferred egress-proxy work — accepted as a residual for the
local, single-operator use this runs in today.

Process discipline mirrors ``recon.discover.harness``: Chromium runs in its own
process group and a wall-clock deadline ``killpg`` s the whole tree (reaping the
renderer / worker / zygote children a plain child-kill would orphan, regardless of
which CDP target we attached to). Every wait loop — port discovery, ws discovery, the
event pump, per-source fetch, and every interaction action — routes through a single
throttled ``_Beater`` so ``on_progress`` fires at most once per ``heartbeat_interval_s``
NO MATTER which phase blocks: the worker renews its job lease (no peer reclaim → no
double browser launch) and observes pause/cancel even during a slow cold start or a
long interaction pass. Host-lane unit tests mock the websocket + ``Popen``; the
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
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from recon.capture import cdp, interaction
from recon.capture.cdp import CaptureError, CdpSession
from recon.observability import get_logger

log = get_logger("recon.capture.driver")

# Windows/test hosts lack SIGKILL; the Linux container (where capture runs) has it.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)
_RECV_TICK_SECONDS = 0.25  # recv poll granularity; bounds progress/interrupt latency
# Minimum drive time before the INITIAL "settled" can fire, so the multi-round-trip
# browser→tab→page→worker attach/navigate handshake is never mistaken for a quiet page.
# A module constant so host tests can shrink it (the real handshake needs the full second).
_MIN_DRIVE_SECONDS = 1.0

# Resource types whose request URL is an API call worth recording for REQ-C3 host
# resolution — XHR + fetch() cover axios / jQuery / fetch (the shapes the static
# extractor traces); Document / Script / Image / Font etc. are page/asset loads.
_REQUEST_TYPES = frozenset({"XHR", "Fetch"})

__all__ = ["CaptureError", "CapturedScript", "CaptureResult", "capture_scripts"]


def _normalize_request_url(url: str) -> str | None:
    """``scheme://netloc/path`` for an http(s) URL, else ``None``. Drops the query +
    fragment so tokens/PII in the query string are never custodied (REQ-S2/S4) — the
    path is all correlation needs — and keeps the port for host-gate accuracy."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"


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
    """The capture outcome. ``nav_error`` is the initial ``Page.navigate`` failure
    text (DNS/TLS/``ERR_*``), or a sentinel when no page target ever attached — the
    stage records the run as ``blocked`` (→ PARTIAL) rather than a false ``ok``/DONE
    with zero scripts. A FAILED route-enum navigation is never fatal (best-effort)."""

    scripts: list[CapturedScript]
    nav_error: str | None
    # REQ-C3: the deduped {method, url} of the XHR/fetch requests the page tree issued
    # (url = scheme://host/path, query dropped), for runtime host resolution. Defaulted
    # so slice-2 fakes constructing CaptureResult(scripts=, nav_error=) stay valid.
    requests: list[dict] = field(default_factory=list)


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
    max_requests: int = 0,
    interact: bool = False,
    max_scroll_steps: int = 0,
    max_clicks: int = 0,
    max_routes: int = 0,
    on_progress: Callable[[int], None] = lambda _n: None,
) -> CaptureResult:
    """Launch headless Chromium, navigate to ``target_url``, optionally drive
    interaction, and return the executed scripts across the whole target tree (deduped
    by content SHA-256) plus any initial-navigation error.

    ``interact`` (+ the ``max_*`` bounds) enables the interaction driver. It defaults
    OFF so an un-driven call behaves exactly like slice 2 (passive capture).

    ``on_progress(n_scripts)`` is invoked before launch, at most once per
    ``heartbeat_interval_s`` throughout (via ``_Beater``), and at end; it may raise
    (the stage's pause/cancel check) to abort — Chromium is always killed in
    ``finally``. Raises :class:`CaptureError` on launch / port / connect failure."""
    deadline = time.perf_counter() + session_budget_s
    beater = _Beater(on_progress, heartbeat_interval_s)
    beater.force(0)  # early interrupt check + first beat, before paying the launch cost
    cfg = (
        interaction.InteractConfig(
            enabled=True,
            max_scroll_steps=max_scroll_steps,
            max_clicks=max_clicks,
            max_routes=max_routes,
            idle_settle_s=idle_settle_s,
            nav_timeout_s=nav_timeout_s,
        )
        if interact
        else None
    )
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
            max_requests=max_requests,
            cfg=cfg,
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
    max_requests: int,
    cfg: interaction.InteractConfig | None,
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
        ctx = _Ctx(
            CdpSession(ws),
            target_url=target_url,
            deadline=deadline,
            idle_settle_s=idle_settle_s,
            min_drive_s=_MIN_DRIVE_SECONDS,
            max_scripts=max_scripts,
            max_script_bytes=max_script_bytes,
            max_requests=max_requests,
            beater=beater,
        )
        # Kick off the tree: auto-attach at the browser level with the ROOT filter
        # (allows `tab` + workers/SWs, excludes `page`; the waterfall reaches the page
        # through its tab — see cdp.ROOT_AUTO_ATTACH_PARAMS for why two filters).
        ctx.loop_start = time.perf_counter()
        ctx.last_event = ctx.loop_start
        ctx.state.send("Target.setAutoAttach", cdp.ROOT_AUTO_ATTACH_PARAMS)
        # Initial load: pump until the tree settles (bounded by nav_timeout for the
        # first navigation), holding off "settled" until the handshake has had a beat.
        ctx.pump(
            settle=True,
            phase_deadline=min(deadline, time.perf_counter() + nav_timeout_s),
            require_min_drive=True,
        )
        if not ctx.navigated and ctx.nav_error is None:
            # No page target ever attached (e.g. a filter/target-topology regression):
            # surface it as blocked rather than a silent "ok" with zero scripts.
            ctx.nav_error = "capture never attached to a page target"
        if (
            cfg is not None
            and cfg.enabled
            and ctx.navigated
            and ctx.nav_error is None
            and ctx.page_session is not None
        ):
            interaction.run(ctx, cfg)
            # Flush any source replies still in flight from the last interaction action
            # (settle already requires an empty in-flight set, but the final action may
            # leave one outstanding).
            ctx.pump(
                settle=True, phase_deadline=min(deadline, time.perf_counter() + idle_settle_s * 2)
            )
        return CaptureResult(scripts=ctx.out, nav_error=ctx.nav_error, requests=ctx.requests)


class _Ctx:
    """Mutable capture state + the single event pump, threaded through the initial
    collect and the interaction driver.

    Fetch-on-parse: a ``Debugger.scriptParsed`` records ``(sessionId, scriptId)`` meta
    AND immediately issues ``Debugger.getScriptSource``; the reply (matched by BARE id,
    since Chrome may omit ``sessionId`` — Puppeteer #14975) appends the deduped source.
    Fetching eagerly is what lets route-enum navigate the page repeatedly without
    stranding earlier routes' scripts (a navigation destroys their V8 context)."""

    def __init__(
        self,
        state: CdpSession,
        *,
        target_url: str,
        deadline: float,
        idle_settle_s: float,
        min_drive_s: float,
        max_scripts: int,
        max_script_bytes: int,
        max_requests: int,
        beater: _Beater,
    ) -> None:
        self.state = state
        self.target_url = target_url
        self.deadline = deadline
        self.idle_settle_s = idle_settle_s
        self.min_drive_s = min_drive_s
        self.max_scripts = max_scripts
        self.max_script_bytes = max_script_bytes
        self.max_requests = max_requests
        self.beater = beater
        self.types: dict[str | None, str] = {}
        self.detached: set[str] = set()
        self.pending_fetch: dict[int, dict] = {}  # getScriptSource request id -> parsed meta
        self.parsed_keys: set[tuple[str | None, str]] = set()  # (sessionId, scriptId) seen
        self.out: list[CapturedScript] = []
        self.seen_sha: set[str] = set()
        self.requests: list[dict] = []  # REQ-C3: deduped {method, url} observed requests
        self.seen_requests: set[tuple[str, str]] = set()
        self.nav_error: str | None = None
        self.navigated = False
        self.nav_id: int | None = None  # the INITIAL Page.navigate id (route navs are not fatal)
        self.page_session: str | None = None
        self.loop_start = 0.0
        self.last_event = 0.0

    def pump(
        self,
        *,
        want_id: int | None = None,
        settle: bool = False,
        phase_deadline: float,
        require_min_drive: bool = False,
    ) -> dict | None:
        """The one event loop. Routes attaches / parses (+ eager source fetch) / source
        replies / nav acks / detaches, beating every iteration. Returns the reply frame
        when ``want_id`` is given and arrives; otherwise ``None`` on settle / deadline /
        a closed socket / an initial-nav error."""
        min_until = (self.loop_start + self.min_drive_s) if require_min_drive else None
        while True:
            now = time.perf_counter()
            self.beater.maybe(len(self.out))  # may raise (pause/cancel) — propagated
            if now > self.deadline or now > phase_deadline or self.nav_error is not None:
                return None
            if settle and self._settled(now, min_until):
                return None
            try:
                msg = self.state.recv(_RECV_TICK_SECONDS)
            except ConnectionClosed:
                return None  # browser went away — return what parsed so far
            if msg is None:
                continue
            reply_id = self._route(msg)
            if want_id is not None and reply_id == want_id:
                return msg

    def _settled(self, now: float, min_until: float | None) -> bool:
        # Settle only after navigation, once scripts have arrived, and once every
        # in-flight source fetch has resolved — else we would stop with un-fetched
        # scripts. ``min_until`` holds off the initial settle through the handshake.
        return (
            self.navigated
            and bool(self.parsed_keys)
            and not self.pending_fetch
            and (now - self.last_event) > self.idle_settle_s
            and (min_until is None or now > min_until)
        )

    def _route(self, msg: dict) -> int | None:
        method = msg.get("method")
        if method == "Target.attachedToTarget":
            self._on_attached(msg["params"])
            self.last_event = time.perf_counter()
            return None
        if method == "Debugger.scriptParsed":
            self._on_parsed(msg)
            return None
        if method == "Network.requestWillBeSent":
            self._on_request(msg)
            return None
        if method == "Target.detachedFromTarget":
            sid = msg["params"].get("sessionId")
            if sid:
                self.detached.add(sid)
                # Drop any in-flight source fetches for the gone session — a detach may
                # not draw an error reply, and a stranded pending entry would keep
                # `_settled` False forever (every settle would then run to its full
                # timeout instead of the quiet window).
                self.pending_fetch = {
                    rid: m for rid, m in self.pending_fetch.items() if m["session"] != sid
                }
            return None
        reply_id = msg.get("id")
        if reply_id is None:
            return None  # some other event we don't act on
        if reply_id in self.pending_fetch:
            self._on_source(reply_id, msg)
            return None  # source replies are internal, never awaited by an action
        if self.nav_id is not None and reply_id == self.nav_id:
            self._on_nav_ack(msg)
        return reply_id  # a command ack (initial nav, route nav, or an eval) — deliver it

    def _on_attached(self, params: dict) -> None:
        """Record a target's type, waterfall auto-attach onto it, enable Debugger (if
        debuggable), navigate the first page, and release it if paused — enabling
        BEFORE releasing so the first ``scriptParsed`` is never missed. Runs for EVERY
        attach, including workers/SWs spawned mid-interaction by a click or a route."""
        session_id = params["sessionId"]
        info = params.get("targetInfo", {})
        ttype = info.get("type", "")
        self.types[session_id] = ttype
        # Waterfall: setAutoAttach is per-session, so re-arm it (CHILD filter, which
        # allows `page`) on every child to reach the page under its `tab` parent and
        # any nested workers.
        self.state.send("Target.setAutoAttach", cdp.CHILD_AUTO_ATTACH_PARAMS, session_id=session_id)
        if ttype in cdp.DEBUGGABLE_TYPES:
            self.state.send("Debugger.enable", session_id=session_id)
            # REQ-C3: also record the request URLs this context issues. Per-session like
            # Debugger, enabled BEFORE the release below, and fire-and-forget (a target
            # that rejects it just drops the error reply — never fatal).
            self.state.send("Network.enable", session_id=session_id)
        if ttype == "page" and not self.navigated:
            self.state.send("Page.enable", session_id=session_id)
            self.nav_id = self.state.send(
                "Page.navigate", {"url": self.target_url}, session_id=session_id
            )
            self.page_session = session_id
            self.navigated = True
        if params.get("waitingForDebugger"):
            # Release AFTER enable — a released target can parse+GC before Debugger is on.
            self.state.send("Runtime.runIfWaitingForDebugger", session_id=session_id)

    def _on_parsed(self, msg: dict) -> None:
        if len(self.parsed_keys) >= self.max_scripts:
            return  # cap reached — ignore further parses (interaction stops cleanly too)
        params = msg["params"]
        sid = msg.get("sessionId")
        key = (sid, params["scriptId"])
        if key in self.parsed_keys:
            return
        self.parsed_keys.add(key)
        self.last_event = time.perf_counter()
        if sid in self.detached:
            return  # the parsing session is gone — its source can't be fetched
        # Eager fetch: ask for the source NOW, before a later navigation destroys the
        # context. The reply is matched by bare id in _route.
        request_id = self.state.send(
            "Debugger.getScriptSource", {"scriptId": params["scriptId"]}, session_id=sid
        )
        self.pending_fetch[request_id] = {
            "session": sid,
            "url": params.get("url", "") or "",
            "sourceMapURL": params.get("sourceMapURL") or None,
            "target_type": self.types.get(sid, "page"),
        }

    def _on_source(self, request_id: int, msg: dict) -> None:
        info = self.pending_fetch.pop(request_id)
        if "error" in msg:
            return
        source = (msg.get("result") or {}).get("scriptSource")
        if not source:
            return
        raw = source.encode("utf-8")
        if len(raw) > self.max_script_bytes:
            # Truncate on a codepoint boundary so a >cap script never stores invalid
            # trailing UTF-8 (the sha — and thus the blob key — is of the stored bytes).
            raw = raw[: self.max_script_bytes].decode("utf-8", "ignore").encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        if digest in self.seen_sha:
            return
        self.seen_sha.add(digest)
        self.out.append(
            CapturedScript(
                url=info["url"],
                source=raw,
                source_map_url=info["sourceMapURL"],
                sha256=digest,
                target_type=info["target_type"],
            )
        )
        self.last_event = time.perf_counter()

    def _on_request(self, msg: dict) -> None:
        """Record the method + URL of one XHR/fetch request the page tree issued, for
        REQ-C3 host resolution. TOTAL by construction — a malformed frame is skipped,
        never raised, so one bad event can't abort the capture (which would lose every
        script). Does NOT bump ``last_event`` or touch ``pending_fetch``: request
        traffic must neither defer the quiet-window settle nor gate it."""
        params = msg.get("params") or {}
        if params.get("type") not in _REQUEST_TYPES:
            return  # only XHR/fetch — not Document/Script/Image/Font page loads
        request = params.get("request") or {}
        url = request.get("url") or ""
        method = request.get("method") or ""
        if not url or not method:
            return
        normalized = _normalize_request_url(url)
        if normalized is None:
            return  # non-http(s) (data:/blob:/ws:) or unparseable
        key = (method, normalized)
        if key in self.seen_requests or len(self.seen_requests) >= self.max_requests:
            return
        self.seen_requests.add(key)
        self.requests.append({"method": method, "url": normalized})

    def _on_nav_ack(self, msg: dict) -> None:
        # Only the INITIAL navigation sets nav_error (→ the run is "blocked"). A hard
        # failure returns errorText (or a protocol error). Route-enum navigations use
        # their own ids and are best-effort — a failed route is skipped, not fatal.
        if "error" in msg:
            self.nav_error = str(msg["error"].get("message", "navigate error"))
        elif (msg.get("result") or {}).get("errorText"):
            self.nav_error = str(msg["result"]["errorText"])

    # ---- interaction-facing helpers (used by recon.capture.interaction) ----

    def evaluate(self, expression: str, *, timeout_s: float) -> dict | None:
        """Run one ``Runtime.evaluate`` in the page session and return its RemoteObject
        result (``{"type","value",...}``), or ``None`` on protocol error / timeout /
        a destroyed context. Bounded per-call so an eval on a navigating page can't
        wedge the loop; the pump keeps beating and capturing parses meanwhile."""
        if self.page_session is None or self.page_session in self.detached:
            return None
        request_id = self.state.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": False},
            session_id=self.page_session,
        )
        reply = self.pump(
            want_id=request_id,
            phase_deadline=min(self.deadline, time.perf_counter() + timeout_s),
        )
        if reply is None or "error" in reply:
            return None
        return (reply.get("result") or {}).get("result") or {}

    def navigate_page(self, url: str, *, timeout_s: float) -> None:
        """Navigate the page session to ``url`` (route-enum). Best-effort: a failed
        navigation is not fatal (unlike the initial one)."""
        if self.page_session is None or self.page_session in self.detached:
            return
        request_id = self.state.send("Page.navigate", {"url": url}, session_id=self.page_session)
        self.pump(
            want_id=request_id, phase_deadline=min(self.deadline, time.perf_counter() + timeout_s)
        )

    def settle(self, *, budget_s: float) -> None:
        """Pump events until the page goes quiet again, after an interaction action.
        Resets the quiet-window baseline first, so a settle from the PREVIOUS action
        can't fire this one instantly (and every in-flight source fetch drains)."""
        self.last_event = time.perf_counter()
        self.pump(settle=True, phase_deadline=min(self.deadline, time.perf_counter() + budget_s))

    def past_deadline(self) -> bool:
        return time.perf_counter() > self.deadline

    def at_cap(self) -> bool:
        # Once max_scripts parses have been seen, _on_parsed drops further ones, so any
        # more interaction only spends budget. Interaction checks this to stop cleanly.
        return len(self.parsed_keys) >= self.max_scripts


def _kill_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), _KILL_SIGNAL)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        log.warning("capture.kill_group_failed", error=str(exc))
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
