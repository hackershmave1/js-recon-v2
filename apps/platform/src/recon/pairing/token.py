"""Stateless signed pairing token — the capture link's credential.

A pairing token lets the Chrome extension route its captured JS into a specific
operator tenant. It is STATELESS: the token itself carries the tenant id + an expiry,
signed with the server key (HMAC-SHA256), so verification needs no database — the
signature proves the server minted it and the payload names the tenant. There is no
per-token revocation store; rotating ``RECON_PAIRING_KEY`` invalidates every
outstanding token (the deliberate "revoke all" control — see the design decision).

Format (compact, JWT-HS256-like without a header): ``<payload_b64>.<sig_b64>`` where
``payload_b64 = b64url(json({"t": tenant_id, "exp": epoch_seconds}))`` and
``sig_b64 = b64url(hmac_sha256(payload_b64, key))``. b64url is unpadded so the token
is copy-paste clean. Verification is constant-time (``hmac.compare_digest``) and fails
CLOSED on any tampering, malformation, or expiry — the caller then falls back to the
shared capture tenant, never erroring open into an operator tenant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _sign(payload_b64: str, key: str) -> str:
    mac = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64url_encode(mac.digest())


def mint(tenant_id: str, *, key: str, ttl_seconds: int, now: float | None = None) -> str:
    """Sign a pairing token granting ``tenant_id`` until ``now + ttl_seconds``."""
    issued = now if now is not None else time.time()
    payload = json.dumps({"t": tenant_id, "exp": int(issued + ttl_seconds)}, separators=(",", ":"))
    payload_b64 = _b64url_encode(payload.encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, key)}"


def verify(token: str, *, key: str, now: float | None = None) -> str | None:
    """Return the granted tenant id for a valid, unexpired token, else ``None``.

    Fails closed on an empty/non-ASCII token, an empty key, a wrong shape, a bad
    signature, a malformed payload, or expiry. Never raises on attacker input.
    """
    if not token or not key or not token.isascii():
        # A real token is base64url (ASCII). A non-ASCII token can't be valid, and the
        # ASCII-only HMAC compare below would RAISE on one (Starlette decodes the
        # Authorization header as latin-1, so bytes 0x80-0xFF arrive as non-ASCII str).
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    payload_b64, sig_b64 = parts
    if not hmac.compare_digest(sig_b64, _sign(payload_b64, key)):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, TypeError):
        return None
    tenant_id = payload.get("t") if isinstance(payload, dict) else None
    exp = payload.get("exp") if isinstance(payload, dict) else None
    if not isinstance(tenant_id, str) or not isinstance(exp, int) or isinstance(exp, bool):
        return None
    if (now if now is not None else time.time()) >= exp:
        return None
    return tenant_id
