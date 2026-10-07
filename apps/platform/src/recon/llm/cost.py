"""Tokens per threat model, for the UI's cost estimate: the team's real history, else a
labelled assumption. Numbers come from data, not prose (CLAUDE.md §5)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from recon.db.base import tenant_session
from recon.db.models import SessionThreatModel

ASSUMED_PROMPT_TOKENS = 20000
ASSUMED_COMPLETION_TOKENS = 4000


def estimate_tokens(tenant_id: str) -> dict[str, Any]:
    with tenant_session(tenant_id) as db:
        avg_prompt, avg_completion, runs = db.execute(
            select(
                func.avg(SessionThreatModel.prompt_tokens),
                func.avg(SessionThreatModel.completion_tokens),
                func.count(),
            ).where(
                SessionThreatModel.tenant_id == uuid.UUID(tenant_id),
                SessionThreatModel.status == "done",
                # Some providers report 0 tokens when usage is missing; don't let that
                # drag the average toward free.
                SessionThreatModel.prompt_tokens > 0,
            )
        ).one()
    if not runs:
        return {
            "prompt_tokens": ASSUMED_PROMPT_TOKENS,
            "completion_tokens": ASSUMED_COMPLETION_TOKENS,
            "basis": "assumed",
            "runs": 0,
        }
    return {
        "prompt_tokens": round(avg_prompt),
        "completion_tokens": round(avg_completion or 0),
        "basis": "history",
        "runs": runs,
    }
