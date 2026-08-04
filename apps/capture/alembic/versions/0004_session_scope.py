"""session_scope

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-21

Adds a first-class scope definition to ``sessions``: ``root_domains`` (a JSON list
of root hostnames the session targets) and ``include_subdomains`` (whether their
subdomains count as in-scope). Existing rows are backfilled from each session's
latest recon job (targets + sameOriginOnly) and, failing that, from the hostnames
of its captured files. See ``api/app/models/session.py``.
"""
import json
from collections import Counter
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    return host[4:] if host.startswith("www.") else host


def upgrade() -> None:
    op.add_column("sessions", sa.Column("root_domains", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("sessions", sa.Column("include_subdomains", sa.Boolean(), nullable=False, server_default=sa.true()))

    bind = op.get_bind()
    sessions_t = sa.table(
        "sessions",
        sa.column("id"),
        sa.column("root_domains", sa.JSON()),
        sa.column("include_subdomains", sa.Boolean()),
    )

    for (sid,) in bind.execute(sa.text("SELECT id FROM sessions")).fetchall():
        include_subdomains = True
        roots: list[str] = []

        job = bind.execute(
            sa.text(
                "SELECT state_json FROM jobs WHERE job_type='recon' AND session_id=:sid "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"sid": str(sid)},
        ).fetchone()
        if job and job[0]:
            state = job[0] if isinstance(job[0], dict) else json.loads(job[0])
            options = state.get("options") or {}
            if "sameOriginOnly" in options:
                include_subdomains = not bool(options.get("sameOriginOnly"))
            for target in state.get("targets") or []:
                host = _host_of(target)
                if host and host not in roots:
                    roots.append(host)

        if not roots:
            counts: Counter = Counter()
            for (url,) in bind.execute(
                sa.text("SELECT url FROM files WHERE session_id=:sid"), {"sid": sid}
            ).fetchall():
                host = _host_of(url)
                if host:
                    counts[host] += 1
            roots = [host for host, _ in counts.most_common(5)]

        bind.execute(
            sessions_t.update().where(sessions_t.c.id == sid).values(
                root_domains=roots, include_subdomains=include_subdomains
            )
        )


def downgrade() -> None:
    op.drop_column("sessions", "include_subdomains")
    op.drop_column("sessions", "root_domains")
