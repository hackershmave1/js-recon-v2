"""D64: the run's endpoint call sites and secret spans, read for ``code_context``.

FindingOccurrence -> RunAsset gives each sighting's file; its ``view_*`` columns are the exact
range in the text the Sources tab serves (``recon.findings.located``), which is the text loaded
here (``probe.sources.get_source_content``), so a window lands on the same lines a user sees.
An occurrence with no exact location (``view_line`` 0, or NULL before migration 0032) gets no
window: a window on the wrong lines would mislead the model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.db.models import Finding, FindingOccurrence, RunAsset
from recon.domain import FindingType
from recon.findings import normalize
from recon.probe import sources
from recon.threat_model.code_context import CallSite, FileRef, SecretSpan

_ENDPOINT_TYPES = (FindingType.ENDPOINT.value, FindingType.ENDPOINT_SUSPECTED.value)
_SECRET_TYPES = (FindingType.SECRET.value, FindingType.SECRET_SUSPECTED.value)
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# DEBT D64's ranking: write / privileged semantics first, then object ids, then auth evidence.
_SENSITIVE_SEGMENT = re.compile(
    r"(create|update|delete|remove|edit|admin|upload|internal|import|export|reset|password|"
    r"token|invite|role|permission|billing|payment|transfer)",
    re.IGNORECASE,
)
_PATH_PARAM = re.compile(r"\{[^}]*\}|/:[A-Za-z_]")


def read_sites(
    db: Session, run_id: str, hidden: set[str]
) -> tuple[list[CallSite], list[SecretSpan]]:
    """One call site per SHOWN endpoint finding (its first exactly located sighting), and every
    secret sighting of the run — hidden ones too: the fence masks what the prompt could quote,
    whatever the workspace shows."""
    rows = db.execute(
        select(
            Finding.id,
            Finding.type,
            Finding.value,
            Finding.attributes,
            FindingOccurrence.source_path,
            FindingOccurrence.view_line,
            FindingOccurrence.view_col,
            FindingOccurrence.view_end_line,
            FindingOccurrence.view_end_col,
            RunAsset.url,
        )
        .join(FindingOccurrence, FindingOccurrence.finding_id == Finding.id)
        .outerjoin(RunAsset, RunAsset.id == FindingOccurrence.run_asset_id)
        .where(Finding.run_id == run_id, Finding.type.in_(_ENDPOINT_TYPES + _SECRET_TYPES))
        .order_by(Finding.id, FindingOccurrence.created_at, FindingOccurrence.id)
    ).all()

    sites: dict[str, CallSite] = {}
    secrets: list[SecretSpan] = []
    for fid, ftype, value, attrs, path, line, col, end_line, end_col, asset_url in rows:
        ref = FileRef(asset_url=asset_url, source_path=path or "input.js")
        if ftype in _SECRET_TYPES:
            rule = attrs.get("rule") if isinstance(attrs, dict) else None
            secrets.append(SecretSpan(ref, line, col, end_line, end_col, value, rule))
            continue
        key = str(fid)
        if key in hidden or key in sites or not line or col is None:
            continue
        attributes = attrs if isinstance(attrs, dict) else {}
        label = endpoint_label(value, attributes)
        sites[key] = CallSite(label, ref, line, col, _priority(label, attributes))
    return list(sites.values()), secrets


def endpoint_label(value: str, attributes: dict[str, Any]) -> str:
    """``[METHOD] /path/{param}`` — the same line the endpoint list shows."""
    method = attributes.get("method") or ", ".join(attributes.get("methods") or [])
    shown = normalize.display_endpoint_value(value, attributes.get("path_params"))
    return f"[{method}] {shown}" if method else shown


def _priority(label: str, attributes: dict[str, Any]) -> int:
    methods = {str(attributes.get("method") or "").upper()} | {
        str(m).upper() for m in attributes.get("methods") or []
    }
    score = 2 if methods & _WRITE_METHODS or _SENSITIVE_SEGMENT.search(label) else 0
    score += 1 if _PATH_PARAM.search(label) else 0
    score += 1 if attributes.get("auth") else 0
    return score


def verify_secret(span: SecretSpan, plaintext: str) -> bool:
    """The text read back at a secret's location hashes to its finding: proof the served text is
    the one analysis scanned (a re-derived beautify could drift) before it is used as the mask."""
    if not span.value or not span.rule:
        return False
    return normalize.normalize_secret_value(plaintext, span.rule) == span.value


def text_loader(tenant_id: str, run_id: str) -> Callable[[FileRef], str | None]:
    """``load_text`` for ``code_context``: a file's served text, exactly as the viewer gets it."""

    def load(ref: FileRef) -> str | None:
        bundle = ref.source_path == "input.js"
        path = (ref.asset_url or "input.js") if bundle else ref.source_path
        content = sources.get_source_content(
            tenant_id, run_id, path, asset_url=None if bundle else ref.asset_url
        )
        return content.content if content is not None else None

    return load
