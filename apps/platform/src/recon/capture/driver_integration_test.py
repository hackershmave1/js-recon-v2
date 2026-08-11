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
