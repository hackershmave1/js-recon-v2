"""Hermetic unit tests for the capture-ingest Origin-lock guard (fast lane).

The guard closes the unauthenticated cross-site WRITE vector into the capture tenant
by rejecting a state-changing ingest POST that carries a web-page (http/https) Origin.
Tested here as a pure function (no app, no DB) so it gates in the fast lane; the
end-to-end wiring through the real /api endpoints is covered in capture_router_test.py
(integration).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from recon.api.capture_router import _enforce_origin_lock


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://evil.example",
        "https://console.fireblocks.io",
        "http://localhost:8000",  # even a same-looking web origin is a web page
    ],
)
def test_web_origin_is_rejected(origin: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _enforce_origin_lock(origin, enabled=True)
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        None,  # no Origin (curl / native / same-origin GET)
        "",  # empty is falsy -> treated as absent
        "chrome-extension://abcdefghijklmnopabcdefghijklmnop",  # the extension worker
        "null",  # opaque/sandboxed-iframe origin — accepted residual (reaches only the
        # fallback capture-spike tenant; operator tenants need a token)
    ],
)
def test_non_web_origin_is_allowed(origin: str | None) -> None:
    # No exception raised == allowed to proceed.
    _enforce_origin_lock(origin, enabled=True)


def test_kill_switch_disables_the_lock() -> None:
    # With the lock off, even a web Origin passes (for a trusted non-browser client).
    _enforce_origin_lock("https://evil.example", enabled=False)


def test_null_origin_is_deliberately_allowed() -> None:
    # DELIBERATE, security-relevant decision (see _enforce_origin_lock NOTE + DEBT.md
    # D17): an opaque `Origin: null` is allowed today because the MV3 worker may emit
    # it, and its residual is bounded to the shared capture-spike tenant (central login
    # re-homes real operator captures into their own tenant).
    # Do not flip without confirming the extension worker's real Origin live and
    # updating the note — this test fails loudly if someone silently changes it.
    _enforce_origin_lock("null", enabled=True)  # no raise


def test_non_string_origin_is_treated_as_absent() -> None:
    # A direct in-process call to a handler (e.g. the concurrency tests) leaves
    # FastAPI's Header sentinel — a non-str — as the arg. The guard must treat it as
    # "no Origin", never try to parse it.
    class _HeaderSentinel:
        pass

    _enforce_origin_lock(_HeaderSentinel(), enabled=True)  # type: ignore[arg-type]
