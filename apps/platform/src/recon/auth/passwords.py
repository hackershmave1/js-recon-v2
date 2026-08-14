"""Password hashing — bcrypt. Never store or compare a plaintext password.

bcrypt hashes are self-describing (the salt + cost live in the ``$2b$...`` string),
so verification needs no separate salt column — ``password_hash`` on ``app_user`` is
the whole credential. NOTE: bcrypt silently truncates input at 72 bytes; that is
irrelevant for the current dev admin and any normal password, but is why a future
"very long passphrase" story would pre-hash. Verification fails CLOSED (returns
False) on a malformed stored hash rather than raising.
"""

from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    """Return a bcrypt hash string safe to store in ``app_user.password_hash``."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str | None) -> bool:
    """True iff ``plain`` matches the stored bcrypt ``hashed``. False (never raises)
    on a missing/malformed hash — a user with no password can never authenticate."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
