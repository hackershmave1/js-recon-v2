"""Single-use handoff code (ADR-0011): lets the extension's "Open in workspace" sign in.

The extension asks for a code bound to the signed-in user + tenant; the workspace
exchanges it ONCE for a normal login token. Properties (each is a test in handoff_test.py):

- 256-bit random code; Redis holds only ``sha256(code)`` as the key (a Redis dump or
  MONITOR never yields a usable code).
- The stored VALUE is an HMAC-signed ``typ:"handoff"`` blob (same format as token.py,
  signed with ``RECON_AUTH_SECRET``): Redis write access alone must not mint a login.
- 30 s TTL enforced by Redis (``SET NX EX``); single use by one atomic ``GETDEL``.
- The blob carries the ISSUING session's expiry so the exchanged session can never
  outlive it (chaining codes cannot lengthen a stolen session).
- Redis errors FAIL CLOSED (``HandoffStoreError``): here Redis is the gate, unlike the
  fail-open login throttle.
- Nothing here logs the code, or any prefix of it.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass

from redis import Redis, RedisError

from recon.auth.token import _b64url_decode, _b64url_encode, _sign

CODE_TTL_SECONDS = 30
ISSUE_LIMIT = 10  # codes per user per window
ISSUE_WINDOW_SECONDS = 60
BLOB_TYPE = "handoff"
# secrets.token_urlsafe(32) is exactly 43 base64url characters; anything else is refused
# before Redis is touched (ADR-0011 review C4).
_CODE_RE = re.compile(r"[A-Za-z0-9_-]{43}")


class HandoffRateLimited(Exception):
    """Too many codes issued for this user in the window."""


class HandoffStoreError(Exception):
    """Redis failed; the caller answers 503 and the researcher uses the login screen."""


@dataclass(frozen=True)
class HandoffGrant:
    user_id: str
    tenant_id: str
    session_exp: int  # the issuing token's expiry — the exchanged session may not exceed it


def is_well_formed(code: str) -> bool:
    return bool(_CODE_RE.fullmatch(code))


def _code_key(code: str) -> str:
    return f"handoff:code:{hashlib.sha256(code.encode('ascii')).hexdigest()}"


def _sign_blob(*, user_id: str, tenant_id: str, session_exp: int, key: str) -> str:
    payload = json.dumps(
        {"typ": BLOB_TYPE, "sub": user_id, "t": tenant_id, "exp": session_exp},
        separators=(",", ":"),
    )
    payload_b64 = _b64url_encode(payload.encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, key)}"


def _verify_blob(blob: str, key: str) -> HandoffGrant | None:
    import hmac

    parts = blob.split(".")
    if len(parts) != 2 or not blob.isascii() or not key:
        return None
    payload_b64, sig = parts
    if not hmac.compare_digest(sig, _sign(payload_b64, key)):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("typ") != BLOB_TYPE:
        return None
    sub, tenant, exp = payload.get("sub"), payload.get("t"), payload.get("exp")
    if not isinstance(sub, str) or not isinstance(tenant, str):
        return None
    if not isinstance(exp, int) or isinstance(exp, bool):
        return None
    return HandoffGrant(user_id=sub, tenant_id=tenant, session_exp=exp)


def issue_code(
    redis: Redis,
    *,
    user_id: str,
    tenant_id: str,
    session_exp: int,
    key: str,
    now: float | None = None,
) -> str:
    """Mint and store a code for ``user_id``/``tenant_id``; raises on rate limit or Redis failure."""
    del now  # TTL is Redis's clock, not ours
    limit_key = f"ratelimit:handoff:issue:{user_id}"
    code = secrets.token_urlsafe(32)
    try:
        pipe = redis.pipeline()
        pipe.incr(limit_key)
        pipe.expire(limit_key, ISSUE_WINDOW_SECONDS)
        count = int(pipe.execute()[0])
        if count > ISSUE_LIMIT:
            raise HandoffRateLimited
        blob = _sign_blob(user_id=user_id, tenant_id=tenant_id, session_exp=session_exp, key=key)
        if not redis.set(_code_key(code), blob, nx=True, ex=CODE_TTL_SECONDS):
            raise HandoffStoreError("code collision")  # 2^-256: treat as a store failure
    except RedisError as exc:
        raise HandoffStoreError(str(exc)) from exc
    return code


def consume_code(
    redis: Redis, code: str, *, key: str, now: float | None = None
) -> HandoffGrant | None:
    """Atomically take the code (``GETDEL``). ``None`` for unknown / used / expired / forged /
    issued-by-an-already-ended-session; the caller must not say which."""
    if not is_well_formed(code):
        return None
    try:
        raw = redis.getdel(_code_key(code))
    except RedisError as exc:
        raise HandoffStoreError(str(exc)) from exc
    if raw is None:
        return None
    blob = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    grant = _verify_blob(blob, key)
    if grant is None:
        return None
    if (now if now is not None else time.time()) >= grant.session_exp:
        return None
    return grant
