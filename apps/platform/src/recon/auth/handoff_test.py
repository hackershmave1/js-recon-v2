"""ADR-0011 conditions for the handoff-code store (hermetic: fakeredis, no DB)."""

from __future__ import annotations

import hashlib
import logging
import threading

import fakeredis
import pytest
from redis import RedisError

from recon.auth import handoff
from recon.auth import token as auth_token

KEY = "handoff-test-secret"
NOW = 1_000_000.0


def _issue(redis, *, now: float = NOW, session_exp: int = int(NOW) + 3600, user: str = "u1"):
    return handoff.issue_code(
        redis, user_id=user, tenant_id="t1", session_exp=session_exp, key=KEY, now=now
    )


def test_code_shape_is_43_base64url_characters() -> None:
    code = _issue(fakeredis.FakeRedis())
    assert handoff.is_well_formed(code) and len(code) == 43
    assert not handoff.is_well_formed(code + "x")
    assert not handoff.is_well_formed("a" * 42 + "!")
    assert not handoff.is_well_formed("")


def test_code_is_stored_hashed_never_in_clear() -> None:
    redis = fakeredis.FakeRedis()
    code = _issue(redis)
    keys = [k.decode() for k in redis.keys("*") if k.startswith(b"handoff:")]
    assert keys == [f"handoff:code:{hashlib.sha256(code.encode()).hexdigest()}"]
    assert code not in keys[0]
    assert all(code.encode() not in (redis.get(k) or b"") for k in redis.keys("*"))


def test_ttl_is_thirty_seconds() -> None:
    redis = fakeredis.FakeRedis()
    _issue(redis)
    (key,) = redis.keys("handoff:code:*")
    assert 0 < redis.ttl(key) <= 30


def test_consume_returns_the_binding_then_never_again() -> None:
    redis = fakeredis.FakeRedis()
    code = _issue(redis)
    grant = handoff.consume_code(redis, code, key=KEY, now=NOW)
    assert grant is not None
    assert (grant.user_id, grant.tenant_id) == ("u1", "t1")
    assert handoff.consume_code(redis, code, key=KEY, now=NOW) is None


def test_unknown_code_is_none() -> None:
    assert handoff.consume_code(fakeredis.FakeRedis(), "A" * 43, key=KEY, now=NOW) is None


def test_single_use_under_a_race() -> None:
    redis = fakeredis.FakeRedis()
    code = _issue(redis)
    wins: list[object] = []
    barrier = threading.Barrier(16)

    def attempt() -> None:
        barrier.wait()
        grant = handoff.consume_code(redis, code, key=KEY, now=NOW)
        if grant is not None:
            wins.append(grant)

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1


def test_expired_code_is_useless_even_if_redis_still_holds_it() -> None:
    redis = fakeredis.FakeRedis()
    code = _issue(redis, now=NOW, session_exp=int(NOW) + 5)
    # The issuing session ended before the exchange: no life remains -> refused.
    assert handoff.consume_code(redis, code, key=KEY, now=NOW + 6) is None


def test_redis_ttl_expiry_removes_the_code() -> None:
    redis = fakeredis.FakeRedis()
    code = _issue(redis)
    (key,) = redis.keys("handoff:code:*")
    redis.expire(key, 1)
    redis.pexpire(key, 1)
    import time

    time.sleep(0.05)
    assert handoff.consume_code(redis, code, key=KEY, now=NOW) is None


def test_planted_unsigned_redis_value_is_refused() -> None:
    # C1: Redis write access alone must not mint a login.
    redis = fakeredis.FakeRedis()
    code = "B" * 43
    redis.set(
        f"handoff:code:{hashlib.sha256(code.encode()).hexdigest()}",
        b'{"typ":"handoff","sub":"admin","t":"t1","exp":9999999999}',
    )
    assert handoff.consume_code(redis, code, key=KEY, now=NOW) is None


def test_value_signed_with_another_key_or_type_is_refused() -> None:
    redis = fakeredis.FakeRedis()
    code = "C" * 43
    forged_auth = auth_token.mint(
        user_id="u1", tenant_id="t1", role="admin", key=KEY, ttl_seconds=3600, now=NOW
    )  # a real login token is NOT a handoff blob
    redis.set(f"handoff:code:{hashlib.sha256(code.encode()).hexdigest()}", forged_auth)
    assert handoff.consume_code(redis, code, key=KEY, now=NOW) is None
    redis2 = fakeredis.FakeRedis()
    code2 = _issue(redis2)
    assert handoff.consume_code(redis2, code2, key="another-key", now=NOW) is None


def test_issue_is_rate_limited_per_user() -> None:
    redis = fakeredis.FakeRedis()
    for _ in range(handoff.ISSUE_LIMIT):
        _issue(redis, user="u1")
    with pytest.raises(handoff.HandoffRateLimited):
        _issue(redis, user="u1")
    _issue(redis, user="u2")  # another user is unaffected


def test_redis_errors_fail_closed() -> None:
    class Broken:
        def __getattr__(self, name):
            raise RedisError("down")

        def pipeline(self):
            raise RedisError("down")

    with pytest.raises(handoff.HandoffStoreError):
        _issue(Broken())
    with pytest.raises(handoff.HandoffStoreError):
        handoff.consume_code(Broken(), "A" * 43, key=KEY, now=NOW)


def test_code_never_appears_in_logs(caplog) -> None:
    redis = fakeredis.FakeRedis()
    with caplog.at_level(logging.DEBUG):
        code = _issue(redis)
        handoff.consume_code(redis, code, key=KEY, now=NOW)
        handoff.consume_code(redis, code, key=KEY, now=NOW)
    assert code not in caplog.text


@pytest.mark.integration
def test_single_use_under_a_race_on_real_redis() -> None:
    from redis import Redis

    from recon.config import get_settings

    redis = Redis.from_url(get_settings().redis_url)
    code = _issue(redis, user=f"race-{threading.get_ident()}")
    wins: list[object] = []
    barrier = threading.Barrier(16)

    def attempt() -> None:
        barrier.wait()
        if handoff.consume_code(redis, code, key=KEY, now=NOW) is not None:
            wins.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
