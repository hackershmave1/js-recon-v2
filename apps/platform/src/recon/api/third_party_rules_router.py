"""Team third-party rules (ADR-0010, R2): ``/settings/third-party-rules``.

* ``GET``: the built-in list (read-only) and the team's additions. Open to the whole team.
* ``POST {kind, value}`` / ``DELETE /{id}``: add or remove an addition. Any signed-in team member
  (the extension's "Ignore for all my sessions" is a researcher's click, not an admin's), recorded
  with the real user. Additions only: nothing here can switch a built-in entry off.
* ``GET /merged``: built-in plus team, flat: what the extension downloads at sign-in and capture start.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_optional_principal, get_principal, get_tenant_id
from recon.auth.service import Principal
from recon.findings import team_rules, third_party_rules

router = APIRouter(tags=["settings"])


_CONFLICT_TEXT = {
    "duplicate": "already on the team list",
    "already_builtin": "already in the built-in list",
}


class RuleIn(BaseModel):
    kind: Literal["host", "file_pattern"]
    value: str


@router.get("/settings/third-party-rules")
async def get_third_party_rules(
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal | None = Depends(get_optional_principal),
) -> dict:
    team = await run_in_threadpool(
        team_rules.list_rules, tenant_id, include_actor=principal is not None
    )
    return {
        "builtin": {
            "hosts": third_party_rules.host_groups(),
            "file_patterns": list(third_party_rules.FILE_PATTERNS),
        },
        "team": team,
        "limits": {
            "hosts": team_rules.MAX_HOSTS,
            "file_patterns": team_rules.MAX_FILE_PATTERNS,
            "pattern_length": team_rules.MAX_PATTERN_LENGTH,
        },
        "can_edit": principal is not None,
    }


@router.get("/settings/third-party-rules/merged")
async def get_merged_third_party_rules(tenant_id: str = Depends(get_tenant_id)) -> dict:
    return await run_in_threadpool(team_rules.merged_for_extension, tenant_id)


@router.post("/settings/third-party-rules", status_code=201)
async def add_third_party_rule(body: RuleIn, principal: Principal = Depends(get_principal)) -> dict:
    try:
        view = await run_in_threadpool(
            team_rules.add_rule, principal.tenant_id, principal.user_id, body.kind, body.value
        )
    except team_rules.TeamRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except team_rules.TeamRuleLimit as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except team_rules.TeamRuleConflict as exc:
        raise HTTPException(status_code=409, detail=_CONFLICT_TEXT[exc.reason]) from exc
    return view


@router.delete("/settings/third-party-rules/{rule_id}", status_code=204)
async def remove_third_party_rule(
    rule_id: str, principal: Principal = Depends(get_principal)
) -> Response:
    removed = await run_in_threadpool(
        team_rules.remove_rule, principal.tenant_id, principal.user_id, rule_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="no such team rule")
    return Response(status_code=204)
