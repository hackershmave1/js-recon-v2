"""Real-Chromium proof for the capture driver (integration lane only — needs the
baked-in /usr/bin/chromium; run inside the app image, not the host lane).

Serves a tiny page exercising the three capture cases the static fetch cannot all
reach, and asserts the driver recovers every one via Debugger.getScriptSource:
  - C1 external <script src>  (also visible to a network fetch)
  - C2 inline <script>        (only in the HTML document, not a separate response)
  - C16 eval(atob(...))       THE litmus: the decoded marker exists in NO served
                              byte, so a network-body search would MISS it.
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
_HTML = (
    "<!doctype html><html><head>"
    "<script>window.__INLINE_MARKER__='INLINE_OK';</script>"
    '<script src="/ext.js"></script>'
    "</head><body>"
    f"<script>eval(atob('{_EVAL_B64}'));</script>"
    "</body></html>"
).encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/ext.js":
            body, ctype = _EXTERNAL, "application/javascript"
        else:
            body, ctype = _HTML, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence the test server
        pass


@pytest.mark.integration
def test_driver_captures_inline_external_and_eval_against_real_chromium():
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            result = driver.capture_scripts(
                f"http://127.0.0.1:{port}/",
                chrome_path="/usr/bin/chromium",
                nav_timeout_s=20.0,
                idle_settle_s=1.0,
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
    assert "EXTERNAL_OK" in blob  # C1 external <script src>
    assert "INLINE_OK" in blob  # C2 inline block
    # C16 litmus: the eval'd script's DECODED source is recovered, though it appears
    # in no served byte (the HTML only carries the base64), and one captured script's
    # source is exactly the decoded eval body.
    assert "EVAL_OK" in blob
    assert _EVAL_SRC in sources
