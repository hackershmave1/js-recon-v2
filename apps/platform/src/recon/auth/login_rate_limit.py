"""Login brute-force throttle for ``POST /auth/login`` (follow-up to central login).

A Redis-backed FAILED-attempt counter, checked BEFORE the bcrypt verify so a login
flood can't burn CPU — every attempt, even an unknown user, otherwise spends one
bcrypt in the enumeration equalizer (``auth/service.authenticate``). Two counters,
both counting only FAILURES within a rolling window and cleared on success:

- **per-username** ``ratelimit:login:user:{sha256(username)}`` — caps password
  guessing against one account without letting attempts on *other* usernames lock it
  out. The username is normalized (case-insensitive) then hashed, so the raw name
  never lands in a key, and it is counted whether or not the user exists — the
  limiter adds no enumeration oracle (mirrors ``authenticate``'s single generic 401).
- **global** ``ratelimit:login:global`` — a flood backstop across all usernames: it
  stops a rotating-username attack from sidestepping the per-user cap, and (each fresh
  username would otherwise get its own per-user bcrypt budget) it bounds the bcrypt CPU
  a rotating flood can burn. TRADEOFF, consciously accepted: being global, an attacker
  who generates ``global_max_attempts`` failures in one window 429s *every* operator's
  login for that window — a self-healing availability DoS. Accepted for an internal
  tool because it is bounded + self-healing per window, fails OPEN on a Redis error, is
  tunable (``global_max_attempts<=0`` disables it), and is strictly better than the
  alternative it replaces — an *unbounded* CPU-exhaustion DoS from rotating usernames
  with no global cap. The password + signed token remain the real access gate.

Deliberately **not** keyed by client IP: the app is served by a bare uvicorn with no
``--proxy-headers`` and real deployments front it with an ingress proxy, so
``request.client.host`` would collapse every client into one bucket — a global
self-DoS (the adversarial design review's blocker B1).

**Fail mode — FAIL-OPEN, by conscious choice.** A Redis error (or the short-timeout
client giving up) lets the login proceed, logged at WARNING. This is defense in
depth, not the access gate: the password + signed token stay fail-closed, so a Redis
blip must never lock every operator out of an internal tool. It is the one narrow,
documented deviation from the repo's fail-closed default (root ``CLAUDE.md``), safe
only because the strong prod password is the primary control (the weak ``admin/admin``
default is refused outside dev — ``bootstrap.py``). The short socket timeout on the
limiter's client (``api/deps.get_login_redis``) bounds the fail-open stall so an
outage never *hangs* a login.

**Not atomic (accepted limitation).** The read-only check and the failure increment
straddle the ~250 ms bcrypt verify, so a tightly concurrent burst can all pass the
check and overshoot the cap for the width of that burst. The cap is defense in depth,
not a hard concurrency gate, so this is acceptable — the password is the real control.

``<=0`` config disables a gate (mirrors ``fetch/politeness``): ``max_attempts<=0``
turns the whole limiter off (the fast lane can run it off); ``global_max_attempts<=0``
turns off just the global backstop.
"""

from __future__ import annotations

import hashlib

from redis import Redis, RedisError

from recon.auth.service import normalize_username
from recon.config import Settings, get_settings
from recon.observability import get_logger

log = get_logger("recon.auth.login_rate_limit")

_GLOBAL_KEY = "ratelimit:login:global"
_USER_KEY = "ratelimit:login:user:{digest}"


def _user_key(username: str) -> str:
    # Hash the NORMALIZED username: one bucket per identity regardless of case, the
    # raw name never lands in a Redis key, and non-existent users get a bucket too
    # (counted uniformly, so no enumeration oracle — see the module docstring).
    digest = hashlib.sha256(normalize_username(username).encode("utf-8")).hexdigest()
    return _USER_KEY.format(digest=digest)


def retry_after_seconds(redis: Redis, username: str, *, settings: Settings | None = None) -> float:
    """Seconds the caller must wait before another login attempt (``0.0`` = allowed).

    READ-ONLY — it inspects the current counters without incrementing (the increment
    happens on a failed attempt via :func:`record_failure`), so an operator who types
    the right password on the first try is never charged. Returns the longest wait
    across the tripped gates. Fails OPEN (``0.0``) on any Redis error."""
    settings = settings or get_settings()
    limit = settings.login_ratelimit_max_attempts
    if limit <= 0:
        return 0.0  # limiter disabled
    checks = [(_user_key(username), limit)]
    global_limit = settings.login_ratelimit_global_max_attempts
    if global_limit > 0:
        checks.append((_GLOBAL_KEY, global_limit))
    try:
        worst = 0.0
        for key, key_limit in checks:
            raw = redis.get(key)
            if raw is None or int(raw) < key_limit:
                continue
            ttl = redis.ttl(key)
            wait = float(ttl) if ttl and ttl > 0 else settings.login_ratelimit_window_seconds
            worst = max(worst, wait)
        return worst
    except (RedisError, OSError, ValueError, TypeError) as exc:
        log.warning("login_rate_limit.check_failed_open", error=str(exc))
        return 0.0


def record_failure(redis: Redis, username: str, *, settings: Settings | None = None) -> None:
    """Count one FAILED login against the per-username and global windows.

    Each ``INCR`` also (re)sets the key's TTL to the full window, so the counter can
    never wedge without an expiry (design review B4) and sustained failures keep the
    lock alive. Best-effort — a Redis error is swallowed (fail open)."""
    settings = settings or get_settings()
    if settings.login_ratelimit_max_attempts <= 0:
        return
    window = max(int(settings.login_ratelimit_window_seconds), 1)
    keys = [_user_key(username)]
    if settings.login_ratelimit_global_max_attempts > 0:
        keys.append(_GLOBAL_KEY)
    try:
        pipe = redis.pipeline()
        for key in keys:
            pipe.incr(key)
            pipe.expire(key, window)
        pipe.execute()
    except (RedisError, OSError) as exc:
        log.warning("login_rate_limit.record_failed_open", error=str(exc))


def clear(redis: Redis, username: str, *, settings: Settings | None = None) -> None:
    """Reset the per-username counter after a SUCCESSFUL login, so an operator who
    fat-fingered a few times isn't throttled once they get in. The GLOBAL counter is
    left to expire on its own — one success must not zero a flood backstop that other
    traffic is filling. Best-effort."""
    settings = settings or get_settings()
    if settings.login_ratelimit_max_attempts <= 0:
        return
    try:
        redis.delete(_user_key(username))
    except (RedisError, OSError) as exc:
        log.warning("login_rate_limit.clear_failed_open", error=str(exc))
