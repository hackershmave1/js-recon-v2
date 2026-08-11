"""Real-Chromium proof for the capture driver (integration lane only — needs the
baked-in /usr/bin/chromium; run inside the app image, not the host lane).

Serves one page that exercises every capture row slice 2 must reach, and asserts the
driver — connected at the BROWSER target and waterfalling Target.setAutoAttach across
the whole tree — recovers each source via Debugger.getScriptSource:
  - C1  external <script src>   (also visible to a network fetch)
  - C2  inline <script>         (only in the HTML document)
  - C16 eval(atob(...))         the decoded marker exists in NO served byte
  - C7  dedicated Web Worker    parses in a worker VM under the page target
  - C8  service worker          attaches at the BROWSER target, not under the page

The page + worker + SW cases in one launch also guard must-fix #1: because we now
connect at the browser level (the page is a child of a `tab` target), the page rows
C1/C2/C16 must NOT regress — proven here through the real browser connection.
"""

from __future__ import annotations

import base64
import http.server
import socketserver
import threading

import pytest

from recon.capture import driver

_EVAL_SRC = "window.__EVAL_MARKER__='EVAL_OK';"
_EVAL_B64 = base64.b64encode(_EVAL_SRC.encode()).decode()
_EXTERNAL = b"window.__EXTERNAL_MARKER__='EXTERNAL_OK';"
_WORKER = b"self.__WORKER_MARKER__='WORKER_OK';"
_SERVICE_WORKER = b"self.__SW_MARKER__='SW_OK';"
_HTML = (
    "<!doctype html><html><head>"
    "<script>window.__INLINE_MARKER__='INLINE_OK';</script>"
    '<script src="/ext.js"></script>'
    "</head><body>"
    f"<script>eval(atob('{_EVAL_B64}'));</script>"
    "<script>"
    "new Worker('/worker.js');"
    "if (navigator.serviceWorker) { navigator.serviceWorker.register('/sw.js'); }"
    "</script>"
    "</body></html>"
).encode()

_ROUTES = {
    "/ext.js": (_EXTERNAL, "application/javascript"),
    "/worker.js": (_WORKER, "application/javascript"),
    "/sw.js": (_SERVICE_WORKER, "application/javascript"),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body, ctype = _ROUTES.get(self.path, (_HTML, "text/html"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A service worker script must be same-origin; no extra headers needed on
        # 127.0.0.1 (a secure context), but disable caching so re-runs are clean.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence the test server
        pass


@pytest.mark.integration
def test_driver_captures_page_worker_and_service_worker_against_real_chromium():
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            result = driver.capture_scripts(
                f"http://127.0.0.1:{port}/",
                chrome_path="/usr/bin/chromium",
                nav_timeout_s=25.0,
                idle_settle_s=2.0,  # allow the SW to install + parse before we settle
                session_budget_s=60.0,
                heartbeat_interval_s=5.0,
                max_scripts=100,
                max_script_bytes=1 << 20,
            )
        finally:
            srv.shutdown()

    assert result.nav_error is None
    sources = [s.source.decode("utf-8", "replace") for s in result.scripts]
    blob = "\n".join(sources)
    # Page rows still captured through the browser-level connection (must-fix #1).
    assert "EXTERNAL_OK" in blob  # C1 external <script src>
    assert "INLINE_OK" in blob  # C2 inline block
    assert "EVAL_OK" in blob  # C16 eval'd source in no served byte
    assert _EVAL_SRC in sources
    # The new slice-2 rows: the worker (C7) and the service worker (C8).
    assert "WORKER_OK" in blob  # C7 dedicated worker VM
    assert "SW_OK" in blob  # C8 service worker (attached at the browser target)
    types = {s.target_type for s in result.scripts}
    assert "service_worker" in types  # provenance proves the SW path, not a page copy


# --- slice 3: the interaction driver reaches JS a passive load never executes ---

_SCROLL_CHUNK = b"window.__SCROLL__='SCROLL_OK';"
_CLICK_CHUNK = b"window.__CLICK__='CLICK_OK';"
_ROUTE_CHUNK = b"window.__ROUTE__='ROUTE_OK';"
# Seed page: an always-present inline marker (so a passive capture is non-empty), a
# button that injects the click chunk, a same-origin route link, a tall spacer, and an
# IntersectionObserver that injects the scroll chunk only once the sentinel is scrolled
# into view. None of the three chunks load on a passive navigation.
_SEED = (
    b"<!doctype html><html><head>"
    b"<script>window.__SEED__='SEED_OK';</script></head><body>"
    b"<button id='load' onclick=\"var s=document.createElement('script');"
    b"s.src='/click-chunk.js';document.body.appendChild(s);\">load</button>"
    b"<a href='/route'>route</a>"
    b"<div style='height:2500px'></div><div id='sentinel'>bottom</div>"
    b"<script>new IntersectionObserver(function(es){es.forEach(function(e){"
    b"if(e.isIntersecting){var s=document.createElement('script');"
    b"s.src='/scroll-chunk.js';document.body.appendChild(s);}});})"
    b".observe(document.getElementById('sentinel'));</script>"
    b"</body></html>"
)
_ROUTE_HTML = b'<!doctype html><html><head><script src="/route-chunk.js"></script></head><body>r</body></html>'
_INTERACT_ROUTES = {
    "/route": (_ROUTE_HTML, "text/html"),
    "/scroll-chunk.js": (_SCROLL_CHUNK, "application/javascript"),
    "/click-chunk.js": (_CLICK_CHUNK, "application/javascript"),
    "/route-chunk.js": (_ROUTE_CHUNK, "application/javascript"),
}


class _InteractionHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body, ctype = _INTERACT_ROUTES.get(self.path, (_SEED, "text/html"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _capture(url: str, *, interact: bool) -> list[str]:
    result = driver.capture_scripts(
        url,
        chrome_path="/usr/bin/chromium",
        nav_timeout_s=25.0,
        idle_settle_s=2.0,
        session_budget_s=90.0,
        heartbeat_interval_s=5.0,
        max_scripts=200,
        max_script_bytes=1 << 20,
        interact=interact,
        max_scroll_steps=12,
        max_clicks=10,
        max_routes=5,
    )
    assert result.nav_error is None
    return [s.source.decode("utf-8", "replace") for s in result.scripts]


@pytest.mark.integration
def test_interaction_driver_reaches_scroll_route_and_click_gated_chunks():
    with socketserver.TCPServer(("127.0.0.1", 0), _InteractionHandler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            driven = "\n".join(_capture(f"http://127.0.0.1:{port}/", interact=True))
            passive = "\n".join(_capture(f"http://127.0.0.1:{port}/", interact=False))
        finally:
            srv.shutdown()

    # Driven capture reaches the scroll-gated, click-gated, and route-split chunks.
    assert "SEED_OK" in driven
    assert "SCROLL_OK" in driven  # IntersectionObserver fired by autoscroll
    assert "CLICK_OK" in driven  # injected by a synthetic click
    assert "ROUTE_OK" in driven  # same-origin route walked (via route-enum or a link click)
    # A passive capture sees only the always-loaded seed script — proving interaction is
    # what unlocks the other three (the whole point of the slice).
    assert "SEED_OK" in passive
    assert "SCROLL_OK" not in passive
    assert "CLICK_OK" not in passive
    assert "ROUTE_OK" not in passive
