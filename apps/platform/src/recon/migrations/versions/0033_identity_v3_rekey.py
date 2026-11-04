"""Carry session state across the finding-identity v2 -> v3 change (placeholder identity)

Revision ID: 0033_identity_v3_rekey
Revises: 0032_occurrence_view_loc
Create Date: 2026-11-04

Identity v3 (``normalize.FINDING_HASH_VERSION = 3``) stores ``GET /users/{id}`` instead of
``GET /users/${userId}``, and the version is hashed into every finding_hash, so every hash
changes. Triage verdicts, spec status and base-URL selection rules are keyed by
(session, finding_hash) to survive re-runs — without this migration the next run would show
every dismissed finding as open again and silently stop applying base-URL picks.

ADDITIVE: each referenced v2 hash gets a v3-keyed COPY (triage, spec status) or its v3 hash
appended (base-URL ``finding_hashes``); the v2 rows stay, so viewing an older run keeps its
verdicts too. When several v2 findings merge into one v3 finding (``${userId}`` and ``${e}``),
the most recently updated verdict wins. Threat citations are left alone: they are verified
against the run they were generated from and rendered as plain hashes, never re-resolved.

The v3 hash is computed here with the version FROZEN at 3 (not ``FINDING_HASH_VERSION``), so a
future identity bump cannot change what this revision did.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op

from recon.findings.normalize import rewrite_v2_value

revision = "0033_identity_v3_rekey"
down_revision = "0032_occurrence_view_loc"
branch_labels = None
depends_on = None


def _hash(version: int, finding_type: str, value: str) -> str:
    # Mirrors normalize.finding_hash with an explicit version.
    canonical = json.dumps(
        {"v": version, "type": finding_type, "value": unicodedata.normalize("NFC", value)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def v3_rekey_map(rows: Iterable[tuple[str, str, str]]) -> dict[str, str]:
    """``{v2 hash: v3 hash}`` for (finding_hash, type, value) rows. Rows that are not v2 (a v1
    path-hashed or an already-v3 row) and findings with no v3 counterpart are skipped."""
    mapping: dict[str, str] = {}
    for finding_hash, finding_type, value in rows:
        if _hash(2, finding_type, value) != finding_hash:
            continue
        new_value = rewrite_v2_value(finding_type, value)
        if new_value is not None:
            mapping[finding_hash] = _hash(3, finding_type, new_value)
    return mapping


_REFERENCED = """
    SELECT DISTINCT f.finding_hash, f.type, f.value FROM finding f
    WHERE f.finding_hash IN (
        SELECT finding_hash FROM finding_triage
        UNION SELECT finding_hash FROM finding_spec_status
        UNION SELECT unnest(finding_hashes) FROM session_base_url WHERE finding_hashes IS NOT NULL
    )
"""


def upgrade() -> None:
    conn = op.get_bind()
    mapping = v3_rekey_map(conn.execute(sa.text(_REFERENCED)).all())
    if not mapping:
        return
    conn.execute(sa.text("CREATE TEMP TABLE identity_v3_map (old text PRIMARY KEY, new text)"))
    conn.execute(
        sa.text("INSERT INTO identity_v3_map (old, new) VALUES (:old, :new)"),
        [{"old": old, "new": new} for old, new in mapping.items()],
    )
    # DISTINCT ON + ORDER BY updated_at DESC: when two v2 findings merge into one v3 finding,
    # the most recent verdict is the one copied; ON CONFLICT keeps an existing v3 row.
    conn.execute(
        sa.text(
            """
            INSERT INTO finding_triage
                (tenant_id, session_id, finding_hash, status, note, actor, created_at, updated_at)
            SELECT DISTINCT ON (t.session_id, m.new)
                t.tenant_id, t.session_id, m.new, t.status, t.note, t.actor,
                t.created_at, t.updated_at
            FROM finding_triage t JOIN identity_v3_map m ON m.old = t.finding_hash
            ORDER BY t.session_id, m.new, t.updated_at DESC
            ON CONFLICT (session_id, finding_hash) DO NOTHING
            """
        )
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO finding_spec_status
                (tenant_id, session_id, finding_hash, status, reason, matched_operation,
                 spec_ref, created_at, updated_at)
            SELECT DISTINCT ON (s.session_id, m.new)
                s.tenant_id, s.session_id, m.new, s.status, s.reason, s.matched_operation,
                s.spec_ref, s.created_at, s.updated_at
            FROM finding_spec_status s JOIN identity_v3_map m ON m.old = s.finding_hash
            ORDER BY s.session_id, m.new, s.updated_at DESC
            ON CONFLICT (session_id, finding_hash) DO NOTHING
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE session_base_url b SET finding_hashes = (
                SELECT array_agg(DISTINCT h) FROM (
                    SELECT unnest(b.finding_hashes) AS h
                    UNION SELECT m.new FROM identity_v3_map m
                    WHERE m.old = ANY (b.finding_hashes)
                ) hashes
            )
            WHERE b.finding_hashes && (SELECT array_agg(old) FROM identity_v3_map)
            """
        )
    )
    conn.execute(sa.text("DROP TABLE identity_v3_map"))


def downgrade() -> None:
    # Additive: the v2 rows were never touched, so there is nothing to undo that a v2 build
    # would read. The v3 copies are harmless to it (no v2 finding carries those hashes).
    pass
