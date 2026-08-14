"""Stateless signed auth-session token — the login credential.

Mirrors the pairing token (``recon.pairing.token``): a compact HMAC-SHA256 token
that carries the authenticated identity — user id (``sub``), tenant id (``t``), and
role — plus an expiry, signed with ``RECON_AUTH_SECRET``. STATELESS: verification
needs no database; the signature proves the server minted it and the payload names
the principal. There is no per-token revocation store — rotating the secret is the
deliberate "revoke all" control (same model as pairing).

Format (compact, JWT-HS256-like without a header): ``<payload_b64>.<sig_b64>`` where
``payload_b64 = b64url(json({"typ":"auth","sub":user_id,"t":tenant_id,"role":role,
"exp":epoch}))`` and ``sig_b64 = b64url(hmac_sha256(payload_b64, key))``.

CRITICAL — token domain separation: this shares the pairing token's wire shape, so
a ``"typ":"auth"`` discriminator is REQUIRED by :func:`verify`. A pairing token has
no ``typ`` and is signed with a different key, so it can never verify here; the app
additionally asserts ``RECON_AUTH_SECRET != RECON_PAIRING_KEY`` at startup as
defence in depth. Verification is constant-time (``hmac.compare_digest``) and fails
CLOSED on any tampering, malformation, missing/typed-wrong claim, or expiry.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

TOKEN_TYPE = "auth"


@dataclass(frozen=True)
class AuthClaims:
    """The verified principal carried by a session token."""

    user_id: str
    tenant_id: str
    role: str


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _sign(payload_b64: str, key: str) -> str:
    mac = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64url_encode(mac.digest())


def mint(
    *,
    user_id: str,
    tenant_id: str,
    role: str,
    key: str,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    """Sign a session token for ``user_id``/``tenant_id``/``role`` valid until now+ttl."""
    issued = now if now is not None else time.time()
    payload = json.dumps(
        {
            "typ": TOKEN_TYPE,
            "sub": user_id,
            "t": tenant_id,
            "role": role,
            "exp": int(issued + ttl_seconds),
        },
        separators=(",", ":"),
    )
    payload_b64 = _b64url_encode(payload.encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, key)}"


def verify(token: str, *, key: str, now: float | None = None) -> AuthClaims | None:
    """Return the :class:`AuthClaims` for a valid, unexpired token, else ``None``.

    Fails closed on an empty/non-ASCII token, an empty key, a wrong shape, a bad
    signature, a malformed payload, a missing/wrong-typed claim, the wrong token
    ``typ`` (e.g. a pairing token), or expiry. Never raises on attacker input.
    """
    if not token or not key or not token.isascii():
        # Starlette decodes the Authorization header as latin-1, so bytes 0x80-0xFF
        # arrive as a non-ASCII str; the ASCII-only HMAC compare below would RAISE
        # on one, so reject it up front (mirrors recon.pairing.token.verify).
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
    if not isinstance(payload, dict):
        return None
    # Require the auth discriminator: a pairing token (no "typ", different key) or
    # any other stray Bearer token can never be accepted as a login credential.
    if payload.get("typ") != TOKEN_TYPE:
        return None
    user_id = payload.get("sub")
    tenant_id = payload.get("t")
    role = payload.get("role")
    exp = payload.get("exp")
    if not isinstance(user_id, str) or not isinstance(tenant_id, str) or not isinstance(role, str):
        return None
    if not isinstance(exp, int) or isinstance(exp, bool):
        return None
    if (now if now is not None else time.time()) >= exp:
        return None
    return AuthClaims(user_id=user_id, tenant_id=tenant_id, role=role)
