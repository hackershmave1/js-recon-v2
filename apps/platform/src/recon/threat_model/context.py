"""Threat model context: the session's latest run rendered as the LLM's markdown prompt."""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select

from recon.db.base import tenant_session
from recon.db.models import EngagementSession, Finding, Run, RunTechnology, SessionSpec
from recon.domain import FindingType, RunState
from recon.findings import normalize, visibility

# Runs with readable findings: the terminal states plus a capture resting between passes.
_TERMINAL_STATES = frozenset(
    {
        RunState.DONE.value,
        RunState.PARTIAL.value,
        RunState.FAILED.value,
        RunState.CANCELLED.value,
        RunState.CAPTURING.value,
    }
)

# Finding types that feed the threat model context.
_ENDPOINT_TYPES = frozenset({FindingType.ENDPOINT.value, FindingType.ENDPOINT_SUSPECTED.value})
_SECRET_TYPES = frozenset({FindingType.SECRET.value, FindingType.SECRET_SUSPECTED.value})
_INFO_TYPES = frozenset(
    {
        FindingType.GRAPHQL.value,
        FindingType.INTERNAL_IP.value,
        FindingType.POSTMESSAGE_SINK.value,
        FindingType.STORAGE_SINK.value,
    }
)


class _Context(NamedTuple):
    markdown: str
    valid_hashes: frozenset[str]
    run_id: str  # the run the prompt describes; the LLM call's bodies are stored under it


def _assemble_context(tenant_id: str, session_id: str) -> _Context:
    """Build the LLM prompt context from the session's latest completed run.

    Returns (context_markdown, valid_finding_hashes). Raises ValueError if no
    terminal run exists for the session yet.
    """
    with tenant_session(tenant_id) as db:
        # Session metadata.
        session_row = db.get(EngagementSession, session_id)
        session_name = (session_row.name or session_id[:8]) if session_row else session_id[:8]
        scope_hosts: list[str] = (session_row.scope_hosts or []) if session_row else []

        # Latest terminal run. Run.id is UUID v4 (random) — sort by created_at.
        run_row = db.execute(
            select(Run.id)
            .where(
                Run.session_id == session_id,
                Run.state.in_(_TERMINAL_STATES),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        ).first()
        if run_row is None:
            raise ValueError("no completed run for this session")
        run_id = str(run_row[0])

        # The run's SHOWN findings — grouped by type. Hidden ones (analytics noise, junk /
        # off-scope page routes — findings/visibility) are not the target's surface.
        hidden = visibility.hidden_finding_ids(db, run_id)
        finding_rows = [
            (ftype, fvalue, fhash, fattrs)
            for fid, ftype, fvalue, fhash, fattrs in db.execute(
                select(
                    Finding.id,
                    Finding.type,
                    Finding.value,
                    Finding.finding_hash,
                    Finding.attributes,
                )
                .where(Finding.run_id == run_id)
                .order_by(Finding.type, Finding.value)
            ).all()
            if str(fid) not in hidden
        ]

        endpoints: list[str] = []
        secret_type_counts: dict[str, int] = {}
        graphql_ops: list[str] = []
        info_findings: dict[str, list[str]] = {}
        valid_hashes: set[str] = set()

        for ftype, fvalue, fhash, fattrs in finding_rows:
            valid_hashes.add(fhash)
            if ftype in _ENDPOINT_TYPES:
                method = ""
                if isinstance(fattrs, dict):
                    m = fattrs.get("method") or (
                        ", ".join(fattrs.get("methods", [])) if fattrs.get("methods") else ""
                    )
                    method = f"[{m}] " if m else ""
                # Display value: `{id}` named from the observed identifier (`{userId}`), so the
                # model keeps the hint a developer's name carries (identity v3).
                shown = normalize.display_endpoint_value(
                    fvalue, (fattrs or {}).get("path_params") if isinstance(fattrs, dict) else None
                )
                endpoints.append(f"{method}{shown}")
            elif ftype in _SECRET_TYPES:
                # REQ-S2: never surface raw secret values. Count by provider prefix.
                provider_prefix = fvalue.split(":")[0] if ":" in fvalue else ftype
                secret_type_counts[provider_prefix] = secret_type_counts.get(provider_prefix, 0) + 1
            elif ftype == FindingType.GRAPHQL.value:
                op_name = (fattrs or {}).get("operation_name", fvalue) if fattrs else fvalue
                graphql_ops.append(str(op_name))
            elif ftype in _INFO_TYPES:
                info_findings.setdefault(ftype, []).append(fvalue)

        # Technologies.
        tech_rows = db.execute(
            select(RunTechnology.name, RunTechnology.version, RunTechnology.categories)
            .where(RunTechnology.run_id == run_id)
            .order_by(RunTechnology.name)
        ).all()
        techs = [f"{name} {version}" if version else name for name, version, _ in tech_rows]

        # OpenAPI spec summary.
        spec_row = db.execute(
            select(
                SessionSpec.operation_count, SessionSpec.server_bases, SessionSpec.spec_format
            ).where(SessionSpec.session_id == session_id)
        ).first()

    # Build the markdown context.
    lines: list[str] = [
        f"# Recon Surface: {session_name}",
        "",
        f"**In-scope hosts:** {', '.join(scope_hosts) or '(not specified)'}",
        "",
    ]

    lines += [f"## API Endpoints ({len(endpoints)} total)"]
    if endpoints:
        for ep in endpoints[:500]:  # cap to avoid context explosion
            lines.append(f"- {ep}")
        if len(endpoints) > 500:
            lines.append(f"- ... and {len(endpoints) - 500} more")
    else:
        lines.append("No endpoints discovered.")
    lines.append("")

    total_secrets = sum(secret_type_counts.values())
    lines += [f"## Secrets Detected ({total_secrets} total — values redacted)"]
    if secret_type_counts:
        for stype, count in sorted(secret_type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {stype}: {count}")
    else:
        lines.append("No secrets detected.")
    lines.append("")

    if graphql_ops:
        lines += [f"## GraphQL Operations ({len(graphql_ops)})"]
        for op in graphql_ops[:50]:
            lines.append(f"- {op}")
        lines.append("")

    if techs:
        lines += [f"## Technologies Detected ({len(techs)})"]
        for t in techs:
            lines.append(f"- {t}")
        lines.append("")

    for ftype, vals in info_findings.items():
        label = {
            FindingType.INTERNAL_IP.value: "Internal IP Literals",
            FindingType.POSTMESSAGE_SINK.value: "postMessage Sinks",
            FindingType.STORAGE_SINK.value: "Web Storage / Cookie Sinks",
        }.get(ftype, ftype)
        lines += [f"## {label} ({len(vals)})"]
        for v in vals[:30]:
            lines.append(f"- {v}")
        lines.append("")

    if spec_row:
        op_count, server_bases, spec_format = spec_row
        lines += [
            "## OpenAPI Spec",
            f"Format: {spec_format} · {op_count} operations · "
            f"Bases: {', '.join(server_bases) if server_bases else '(none)'}",
            "",
        ]
    else:
        lines += ["## OpenAPI Spec", "No spec uploaded for this session.", ""]

    return _Context("\n".join(lines), frozenset(valid_hashes), run_id)
