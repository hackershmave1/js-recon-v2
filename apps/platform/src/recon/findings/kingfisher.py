"""Kingfisher secret-scanning adapter (out-of-process engine).

Runs MongoDB Kingfisher over the run's JS blob and turns its JSONL output into
``RawSecret`` records the analyze stage normalizes into SECRET findings. The raw
token never enters the finding identity in cleartext — ``normalize_secret_value``
hashes it (see ``recon.findings.normalize`` §4.2).

Kingfisher facts pinned to ``kingfisher-bin==1.106.0`` (see pyproject):
- Output is JSONL: the line whose ``findings`` is a *list* carries the findings;
  a separate summary line has ``findings`` as an *int*.
- Exit code 0 = ran/no secrets, 200 = ran/secrets found, other = real error.
- ``--no-validate`` + ``--no-update-check`` keep it offline (MVP "no network");
  ``--no-dedup`` reports every sighting so REQ-C2 occurrence honesty holds.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from recon.config import get_settings
from recon.findings import engines
from recon.observability import get_logger

log = get_logger("recon.findings.kingfisher")

# 0 = clean, 200 = secrets found. Anything else is a genuine engine error.
_OK_RETURNCODES = (0, 200)

# A shipped custom rule so a STANDALONE AWS Access Key ID (AKIA…) emits as a
# finding. Kingfisher's built-in AWS rule treats the access-key id as a pairing
# "identity" and, under --no-validate, never emits a lone id — it only tallies it
# in the summary's findings_by_rule (never the findings array we parse) — so a
# leaked AKIA in a target's JS was silently missed. Resolved to a STABLE path
# (packaged via pyproject [tool.setuptools.package-data]) so the compiled-rule
# cache and the Dockerfile pre-warm stay effective; provider pinned to "aws" in
# normalize._PROVIDER_BY_RULE.
_RULES_PATH = Path(__file__).parent / "rules" / "aws_access_key_id.yml"


@dataclass(frozen=True)
class RawSecret:
    """One secret sighting straight from Kingfisher, before normalization."""

    rule_id: str
    rule_name: str
    snippet: str
    confidence: str | None = None
    entropy: str | None = None
    fingerprint: str | None = None
    line: int | None = None
    column_start: int | None = None
    column_end: int | None = None
    validation_status: str | None = None


@dataclass(frozen=True)
class ScanResult:
    """Outcome of a scan. ``status`` is surfaced for honesty (REQ-C2/§5).

    Only two statuses reach the caller: ``ok`` (scan ran) and ``unavailable``
    (the binary is not installed). A genuine engine failure (bad exit / timeout /
    oversized output) is *raised*, not swallowed, so the analyze stage fails and
    retries rather than silently reporting "no secrets" while marking the run
    complete — which would let REQ-D5 later report a live secret as removed.

    NOTE (follow-up, REQ-D5 completeness): ``unavailable`` is treated as soft
    because the scanner is a pinned dependency, so absence is a deployment
    anomaly, not a per-run outcome. If secret scanning ever becomes optional per
    deployment, ``unavailable`` must flow into ``run.completeness.analyze_ok`` so
    a run without secret coverage cannot license "removed" in a diff."""

    secrets: list[RawSecret] = field(default_factory=list)
    status: str = "ok"  # ok | unavailable


def parse_findings(stdout: bytes) -> list[RawSecret]:
    """Parse Kingfisher JSONL into ``RawSecret`` records (pure, defensive).

    Skips the summary line (``findings`` is an int there) and any non-JSON log
    line; keeps only records that carry both a rule id and a matched snippet.
    """
    secrets: list[RawSecret] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue  # a stray non-JSON log line — ignore
        findings = envelope.get("findings") if isinstance(envelope, dict) else None
        if not isinstance(findings, list):
            continue  # the summary line (findings is an int) or an unrelated line
        for entry in findings:
            secret = _to_raw_secret(entry)
            if secret is not None:
                secrets.append(secret)
    return secrets


def _to_raw_secret(entry: object) -> RawSecret | None:
    if not isinstance(entry, dict):
        return None
    rule = entry.get("rule")
    finding = entry.get("finding")
    if not isinstance(rule, dict) or not isinstance(finding, dict):
        return None
    rule_id = rule.get("id")
    snippet = finding.get("snippet")
    if not rule_id or not snippet:
        return None
    validation = finding.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    return RawSecret(
        rule_id=str(rule_id),
        rule_name=str(rule.get("name") or ""),
        snippet=str(snippet),
        confidence=_opt_str(finding.get("confidence")),
        entropy=_opt_str(finding.get("entropy")),
        fingerprint=_opt_str(finding.get("fingerprint")),
        line=_opt_int(finding.get("line")),
        column_start=_opt_int(finding.get("column_start")),
        column_end=_opt_int(finding.get("column_end")),
        validation_status=_opt_str(validation.get("status")),
    )


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def byte_offset(source: str, line: int | None, column: int | None) -> int | None:
    """UTF-8 byte offset from Kingfisher's 1-based line and 0-based column.

    Kingfisher reports line/column but no raw offset; occurrence identity keys on
    offsets (see ``store.Occurrence._identity``), so we derive one from the source
    to keep two sightings of the same secret distinct (REQ-C2). Returns a true
    *byte* offset — the same unit Vespasian stores for endpoints (tree-sitter
    ``start_byte``) — so the two never carry mismatched units in the same column.
    Lines are split on ``\\n`` only (Kingfisher's line semantics), not on the
    extra separators ``str.splitlines`` recognizes."""
    if line is None or line < 1:
        return None
    lines = source.split("\n")
    if line > len(lines):
        return None
    prefix = "\n".join(lines[: line - 1])
    if line > 1:
        prefix += "\n"  # the separator that ends the preceding line
    prefix += lines[line - 1][: column or 0]
    return len(prefix.encode("utf-8"))


def locate_snippet(
    source: str, snippet: str, *, search_from: int = 0
) -> tuple[int, int] | None:
    """Byte ``[start, end)`` of ``snippet`` within ``source``, located by CONTENT.

    In the utf-8/replace byte space reveal slices (see ``recon.probe.reveal``:
    ``source == raw.decode("utf-8","replace")``, so ``source.encode("utf-8")``
    reproduces it). We do NOT derive this from Kingfisher's line/column: those mark
    the rule's *match region*, which for some rules (e.g. an AWS secret-access-key
    keyed off a nearby access-key id) is not where the extracted ``snippet`` lives —
    a line/column offset then slices the wrong bytes and an audited reveal
    fail-closes 409. Locating the exact snippet bytes guarantees the round-trip:
    ``data[start:end]`` IS the snippet, so ``normalize_secret_value`` matches the
    finding identity. ``search_from`` lets N identical sightings map to N distinct
    offsets (occurrence honesty, REQ-C2). Returns ``None`` if the snippet is not
    present verbatim — the finding is then stored offset-less (reveal -> 422)."""
    if not snippet:
        return None
    data = source.encode("utf-8")
    needle = snippet.encode("utf-8")
    idx = data.find(needle, search_from)
    if idx < 0:
        return None
    return idx, idx + len(needle)


def scan(
    source: bytes,
    *,
    bin_path: str | None = None,
    timeout_s: float | None = None,
    max_output_bytes: int | None = None,
) -> ScanResult:
    """Scan ``source`` for secrets.

    Returns ``status="unavailable"`` (soft) if the binary is missing. Re-raises
    :class:`engines.EngineError`/:class:`engines.EngineTimeout` for a genuine
    failure so the analyze stage fails/retries instead of silently under-reporting.
    """
    settings = get_settings()
    bin_path = bin_path or settings.kingfisher_bin
    timeout_s = timeout_s if timeout_s is not None else settings.engine_timeout_seconds
    max_output_bytes = (
        max_output_bytes if max_output_bytes is not None else settings.engine_max_output_bytes
    )

    with tempfile.TemporaryDirectory(prefix="kf-") as workdir:
        target = os.path.join(workdir, "input.js")
        with open(target, "wb") as handle:
            handle.write(source)
        # Scan the file (not the dir) so no sibling/symlink is ever walked.
        argv = [
            engines.resolve_bin(bin_path), "scan", target,
            "--format", "json",
            "--no-validate", "--no-update-check", "--no-dedup",
        ]
        # Load the shipped standalone-AKIA rule. A missing file is a PACKAGING
        # regression (the wheel dropped the data file), not a per-run condition, so
        # degrade LOUDLY to the built-in ruleset — we still scan, only losing
        # standalone AWS-key-id detection — rather than passing a bad --rules-path
        # that makes kingfisher exit 1 and hard-fails every scan.
        if _RULES_PATH.is_file():
            argv += ["--rules-path", str(_RULES_PATH)]
        else:
            log.error("kingfisher.rules_missing", path=str(_RULES_PATH))
        try:
            result = engines.run_engine(
                argv,
                timeout_s=timeout_s,
                max_output_bytes=max_output_bytes,
                ok_returncodes=_OK_RETURNCODES,
            )
        except engines.EngineNotAvailable:
            # Soft: the pinned binary is absent (a deployment anomaly). Every
            # other failure re-raises to fail/retry the stage — see ScanResult.
            log.warning("kingfisher.unavailable", bin=bin_path)
            return ScanResult(status="unavailable")

    secrets = parse_findings(result.stdout)
    log.info("kingfisher.done", secrets=len(secrets))
    return ScanResult(secrets=secrets, status="ok")
