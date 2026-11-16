"""The team's additions to the built-in third-party rules (ADR-0010, R2).

The built-in list is ``third_party_rules.json``. The team can only ADD to it here: a host (a script
from it is dropped unless the researcher typed that host into a session scope) or a file pattern
(applies on any host). Nothing in this module can switch a built-in entry off, and the merge
(:meth:`TeamRules.noise_hosts`, :meth:`TeamRules.file_patterns_all`) is always built-in plus team.

Validation is at the edge: a host goes through the egress scope-entry normalization (so a bare TLD,
an IP, a public suffix, a URL or a wildcard-only entry is refused); a pattern is bounded in length,
count and wildcard number, must carry enough literal text that it cannot drop every script, and is
matched by the non-regex glob in ``third_party_rules`` so a hostile URL cannot make it backtrack.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import AppUser, TenantThirdPartyRule
from recon.fetch import egress
from recon.findings import third_party_rules
from recon.observability import get_logger

log = get_logger("recon.findings.team_rules")

Kind = Literal["host", "file_pattern"]
MAX_HOSTS = 200
MAX_FILE_PATTERNS = 100
MAX_PATTERN_LENGTH = 200
MAX_WILDCARDS = 5
MIN_LITERAL_CHARS = 4  # `/*`, `*.js` and the like would drop every script


class TeamRuleError(ValueError):
    """The value is not an acceptable rule (the router maps it to 422)."""


class TeamRuleConflict(Exception):
    """The rule already exists, or is already built in (409). ``reason`` says which."""

    def __init__(self, reason: Literal["duplicate", "already_builtin"]) -> None:
        super().__init__(reason)
        self.reason = reason


class TeamRuleLimit(Exception):
    """The team is at its cap for this kind (422)."""


def validate_host(value: str) -> str:
    host = egress.normalize_scope_entry(value or "", allow_local=get_settings().allow_local_egress)
    if host is None:
        raise TeamRuleError("not a usable host: use a bare host like tracker.example.com")
    return host


def validate_pattern(value: str) -> str:
    pattern = (value or "").strip()
    if not pattern:
        raise TeamRuleError("pattern is empty")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise TeamRuleError(f"pattern must be at most {MAX_PATTERN_LENGTH} characters")
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in pattern):
        raise TeamRuleError("pattern must not contain spaces or control characters")
    if "://" in pattern:
        raise TeamRuleError("a pattern matches a host or a path, not a full URL")
    while "**" in pattern:
        pattern = pattern.replace("**", "*")
    if pattern.count("*") > MAX_WILDCARDS:
        raise TeamRuleError(f"pattern may have at most {MAX_WILDCARDS} wildcards")
    if sum(1 for ch in pattern if ch not in "*/") < MIN_LITERAL_CHARS:
        raise TeamRuleError(
            f"pattern needs at least {MIN_LITERAL_CHARS} characters besides '*' and '/'"
        )
    return pattern.lower()


@dataclass(frozen=True)
class TeamRules:
    hosts: frozenset[str]
    file_patterns: tuple[str, ...]

    def noise_hosts(self) -> frozenset[str]:
        """Built-in plus team hosts (matched exact or dot-suffix, see ``noise_hosts``)."""
        return third_party_rules.HOSTS | self.hosts

    def file_patterns_all(self) -> tuple[str, ...]:
        return (*third_party_rules.FILE_PATTERNS, *self.file_patterns)


EMPTY = TeamRules(hosts=frozenset(), file_patterns=())


def load_team_rules(session: Session, tenant_id: str) -> TeamRules:
    """The tenant's additions, for the read-time merge in ``visibility``."""
    rows = session.execute(
        select(TenantThirdPartyRule.kind, TenantThirdPartyRule.value)
        .where(TenantThirdPartyRule.tenant_id == uuid.UUID(str(tenant_id)))
        .order_by(TenantThirdPartyRule.value)
    ).all()
    return TeamRules(
        hosts=frozenset(v for k, v in rows if k == "host"),
        file_patterns=tuple(v for k, v in rows if k == "file_pattern"),
    )


def _view(row: TenantThirdPartyRule, email: str | None) -> dict[str, str | None]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "value": row.value,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": email,
    }


def list_rules(tenant_id: str, *, include_actor: bool) -> dict[str, list[dict[str, str | None]]]:
    """The team's additions, grouped by kind. ``created_by`` (an email) only with a real login."""
    with tenant_session(tenant_id) as db:
        rows = db.execute(
            select(TenantThirdPartyRule, AppUser.email)
            .outerjoin(AppUser, AppUser.id == TenantThirdPartyRule.created_by)
            .order_by(TenantThirdPartyRule.value)
        ).all()
        views = [_view(row, email if include_actor else None) for row, email in rows]
    return {
        "hosts": [v for v in views if v["kind"] == "host"],
        "file_patterns": [v for v in views if v["kind"] == "file_pattern"],
    }


def merged_for_extension(tenant_id: str) -> dict[str, object]:
    """What the extension downloads: built-in plus team, flat. The extension keeps its own built-in
    copy, so a failed download still leaves it the built-in list."""
    with tenant_session(tenant_id) as db:
        team = load_team_rules(db, tenant_id)
    return {
        "version": 1,
        "hosts": sorted(team.noise_hosts()),
        "file_patterns": list(team.file_patterns_all()),
        "team_hosts": sorted(team.hosts),
        "team_file_patterns": list(team.file_patterns),
    }


def add_rule(tenant_id: str, user_id: str | None, kind: Kind, value: str) -> dict[str, str | None]:
    if kind == "host":
        clean = validate_host(value)
        if clean in third_party_rules.HOSTS:
            raise TeamRuleConflict("already_builtin")
        cap = MAX_HOSTS
    else:
        clean = validate_pattern(value)
        if clean in {p.lower() for p in third_party_rules.FILE_PATTERNS}:
            raise TeamRuleConflict("already_builtin")
        cap = MAX_FILE_PATTERNS
    try:
        with tenant_session(tenant_id) as db:
            count = db.scalar(
                select(func.count())
                .select_from(TenantThirdPartyRule)
                .where(TenantThirdPartyRule.kind == kind)
            )
            if (count or 0) >= cap:
                raise TeamRuleLimit(f"the team already has {cap} {kind.replace('_', ' ')} rules")
            row = TenantThirdPartyRule(
                tenant_id=uuid.UUID(tenant_id),
                kind=kind,
                value=clean,
                created_by=uuid.UUID(user_id) if user_id else None,
            )
            db.add(row)
            db.flush()
            view = _view(row, None)
    except IntegrityError as exc:
        raise TeamRuleConflict("duplicate") from exc
    # The audit trail for a team-wide rule: who, what, when (structured log + created_by).
    log.info("third_party_rule.added", tenant_id=tenant_id, user_id=user_id, kind=kind, value=clean)
    return view


def remove_rule(tenant_id: str, user_id: str | None, rule_id: str) -> bool:
    try:
        key = uuid.UUID(rule_id)
    except ValueError:
        return False
    with tenant_session(tenant_id) as db:
        row = db.execute(
            delete(TenantThirdPartyRule)
            .where(TenantThirdPartyRule.id == key)
            .returning(TenantThirdPartyRule.kind, TenantThirdPartyRule.value)
        ).first()
    if row is None:
        return False
    log.info(
        "third_party_rule.removed", tenant_id=tenant_id, user_id=user_id, kind=row[0], value=row[1]
    )
    return True
