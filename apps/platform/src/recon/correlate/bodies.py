"""D45b2 — analyze captured request/response BODIES (secret-scan + light param hints).

The capture extension attaches optional per-observation body text (``reqBody`` on by default,
``respBody`` opt-in) to the runtime observations it already sends for endpoint correlation. The
CORRELATING stage stores those bodies (re-capped server-side) on the ``capture-requests`` blob;
this module is the analysis half, run from ``correlate.stage.correlate_run`` AFTER endpoint
correlation has committed:

- **Secret scan (the headline):** run Kingfisher over each body — real tokens/keys live in the
  payloads the static bundle never contains. Each sighting becomes a ``SECRET`` finding with
  ``engine="capture"`` provenance and a synthetic ``capture-request://``/``capture-response://``
  ``source_path``, so it is distinguishable from a bundle secret and never claims a revealable
  byte space (a captured body is not a persisted blob, so reveal fail-closes on it — by design).
- **Light param hints (best-effort):** a REQUEST body that cleanly parses as a JSON object or a
  urlencoded form contributes its TOP-LEVEL keys as ``PARAM`` findings, tied to the endpoint the
  observation's URL matched in correlate (its operation + identity). Conservative on purpose — no
  nested descent, capped fan-out, and skipped when the body is not cleanly parseable. A hint, not
  a schema extraction.

Provenance / trust: a main-world-sourced response body is page-forgeable (a documented design
risk), so nothing here mints a new ``FindingType`` or a confirmed endpoint — it reuses ``SECRET``
/ ``PARAM`` and marks the occurrence ``engine="capture"`` so the origin stays legible. Bounded +
fail-closed: bodies are capped (count here + bytes at ingest), a malformed body is skipped, and
the whole pass is BEST-EFFORT — ``correlate_run`` swallows any failure so body analysis can never
fail the correlate stage (the endpoint occurrences are already durable). Idempotent via the
REQ-A3 outbox upserts in ``store.record_finding`` and RLS-scoped by ``tenant_session``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from redis import Redis
from sqlalchemy.orm import Session

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.events.log import publish, record_event
from recon.findings import kingfisher, normalize, store
from recon.findings import queries as findings_queries
from recon.observability import get_logger

log = get_logger("recon.correlate.bodies")

# A conservative urlencoded-form shape: one-or-more ``key=value`` pairs joined by ``&`` with no
# whitespace anywhere (a real form encodes a space as ``+``/``%20``). Anything else is NOT treated
# as a form, so free-text / HTML / near-JSON bodies never mint phantom params.
_FORM_RE = re.compile(r"^[^=&\s]+=[^&\s]*(?:&[^=&\s]+=[^&\s]*)*$")

# Bound one body's param-key fan-out so a pathological JSON object with thousands of top-level
# keys can't mint thousands of PARAM findings. This is a hint surface, not a schema.
_MAX_PARAM_KEYS_PER_BODY = 50

# Sentinel distinguishing "body was not valid JSON" from "body parsed to JSON null/false/0".
_UNSET: object = object()


@dataclass(frozen=True)
class _Body:
    """One captured body to analyze, paired with the observation it came from."""

    kind: str  # "request" | "response"
    host: str | None
    url: str  # the normalized observed URL (scheme://netloc/path — query already dropped)
    text: str


def analyze_bodies(
    redis: Redis,
    *,
    tenant_id: str,
    run_id: str,
    observed: list[dict],
    resolved: dict[str, str],
    by_hash: dict[str, findings_queries.FindingView],
) -> None:
    """Secret-scan the captured bodies + extract light param hints, writing findings under
    ``run_id`` in this function's OWN transaction. ``resolved`` (``{finding_hash: url}``) and
    ``by_hash`` come from correlate: a param hint attaches to the endpoint finding whose identity
    the observation's URL matched.

    A clean no-op when no observation carries a body. NOT self-guarding against failure — the
    caller (``correlate_run``) wraps this best-effort, so a genuine Kingfisher engine failure may
    propagate out of :func:`kingfisher.scan_many` and be swallowed there (body analysis is
    enrichment atop already-committed endpoint correlation, never a reason to fail the stage)."""
    settings = get_settings()
    bodies = _collect_bodies(observed, settings.capture_max_bodies)
    if not bodies:
        return

    # ONE Kingfisher pass over every body (``scan_many`` forks a single subprocess, not one per
    # body). Scale the output cap with total input so a ``--no-dedup`` scan of many bodies can't
    # trip a false ``EngineError`` on buffered JSONL (mirrors analyze's recovered-tree scan).
    total_bytes = sum(len(b.text.encode("utf-8")) for b in bodies)
    output_cap = max(settings.engine_max_output_bytes, total_bytes)
    secrets_by_index, secrets_engine = kingfisher.scan_many(
        [(b.url, b.text.encode("utf-8")) for b in bodies], max_output_bytes=output_cap
    )

    url_to_hash = {url: finding_hash for finding_hash, url in resolved.items()}
    secrets_written = 0
    params_written = 0
    with tenant_session(tenant_id) as session:
        for index, secrets in secrets_by_index.items():
            secrets_written += _record_body_secrets(
                session, tenant_id=tenant_id, run_id=run_id, body=bodies[index], secrets=secrets
            )
        for body in bodies:
            # Param hints: REQUEST bodies only, and only when the URL matched an endpoint in
            # correlate (so the hint reuses a real endpoint's identity, never a phantom one).
            if body.kind != "request":
                continue
            finding = by_hash.get(url_to_hash.get(body.url, ""))
            if finding is None:
                continue
            params_written += _record_body_params(
                session, tenant_id=tenant_id, run_id=run_id, body=body, finding=finding
            )
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="correlate.bodies",
            # Observability (CLAUDE.md §5): counts + engine status only, never a body or value.
            payload={
                "bodies": len(bodies),
                "secrets": secrets_written,
                "params": params_written,
                "secrets_engine": secrets_engine,
            },
        )
    publish(redis, event)
    log.info(
        "correlate.bodies_done",
        run_id=run_id,
        bodies=len(bodies),
        secrets=secrets_written,
        params=params_written,
        secrets_engine=secrets_engine,
    )


def _collect_bodies(observed: list[dict], cap: int) -> list[_Body]:
    """The capped, ordered list of request/response bodies present on the observations. A
    request body precedes its response body; ``cap`` bounds total bodies scanned per run."""
    bodies: list[_Body] = []
    for obs in observed:
        if not isinstance(obs, dict):
            continue
        url = str(obs.get("url") or "")
        host = urlsplit(url).hostname if url else None
        for kind, field in (("request", "reqBody"), ("response", "respBody")):
            text = obs.get(field)
            if isinstance(text, str) and text:
                bodies.append(_Body(kind=kind, host=host, url=url, text=text))
                if len(bodies) >= cap:
                    return bodies
    return bodies


def _body_source_path(body: _Body) -> str:
    """A synthetic, provenance-carrying ``source_path`` for a captured body's findings:
    ``capture-request://<host><path>`` / ``capture-response://<host><path>``. Deliberately
    distinct from any real bundle/recovered path, so reveal's byte-space selection never mistakes
    it for a sliceable blob — it isn't one, and reveal cleanly denies for these (fail-closed)."""
    path = urlsplit(body.url).path or "/"
    return f"capture-{body.kind}://{body.host or ''}{path}"


def _record_body_secrets(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    body: _Body,
    secrets: list[kingfisher.RawSecret],
) -> int:
    """Record each secret sighting in one body as a ``SECRET`` finding + capture occurrence.

    ``value`` is the same ``provider:sha256(token)`` identity as a bundle secret, so a token seen
    in BOTH a payload and the static bundle dedupes to one finding with distinct occurrences
    (REQ-C2). Stored OFFSET-LESS on purpose: a captured body is not a persisted blob, so there is
    no byte space to reveal from — carrying no ``offset_start`` keeps the sighting out of reveal's
    candidate set (it denies ``no_offsets`` rather than promising bytes it can't reproduce).
    Kingfisher line/col ride along for display + occurrence distinctness. Returns the count of
    secret SIGHTINGS recorded (a stable observability metric — the upserts idempotently no-op on a
    re-run, so this is NOT a new-rows count)."""
    source_path = _body_source_path(body)
    for secret in secrets:
        value = normalize.normalize_secret_value(secret.snippet, secret.rule_id)
        store.record_finding(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            finding_type=FindingType.SECRET,
            value=value,
            path=source_path,
            occurrence=store.Occurrence(
                host=body.host,
                raw_url=body.url,
                source_path=source_path,
                line=secret.line,
                col=secret.column_start,
                engine="capture",  # distinct provenance — a captured payload, not the bundle
                confidence=secret.confidence,
                verified=True if secret.validation_status == "Active" else None,
            ),
            attributes={"rule": secret.rule_id, "name": secret.rule_name},
            first_stage="correlating",
        )
    return len(secrets)


def _record_body_params(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    body: _Body,
    finding: findings_queries.FindingView,
) -> int:
    """Record a request body's TOP-LEVEL keys as ``PARAM`` findings tied to the matched endpoint.

    The param value reuses the matched endpoint's OPERATION (``METHOD /templated/path``) via
    ``operation_of_endpoint_value`` so the hint attaches to that endpoint's identity;
    ``location`` is ``body``. Best-effort: returns 0 for a body that doesn't cleanly parse as a
    JSON object or urlencoded form (see :func:`_top_level_keys`)."""
    keys = _top_level_keys(body.text)
    if not keys:
        return 0
    operation = normalize.operation_of_endpoint_value(finding.value)
    source_path = _body_source_path(body)
    for name in keys:
        value = normalize.normalize_param_value(operation, "body", name)
        store.record_finding(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            finding_type=FindingType.PARAM,
            value=value,
            path=finding.path,  # reuse the matched endpoint's finding path (its source file)
            occurrence=store.Occurrence(
                host=body.host,
                raw_url=body.url,
                source_path=source_path,
                engine="capture",
            ),
            attributes={"location": "body", "name": name},
            first_stage="correlating",
        )
    return len(keys)


def _top_level_keys(body: str) -> list[str]:
    """The TOP-LEVEL parameter names in a request body, or ``[]`` when it doesn't cleanly parse as
    a JSON object or a urlencoded form. Conservative by design (a hint, not a schema): no nested
    descent, capped fan-out, and a body that is valid JSON but NOT an object (a bare
    array/number/string/null) yields nothing rather than guessing."""
    text = body.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = _UNSET
    if isinstance(parsed, dict):
        return _dedupe(str(k) for k in parsed if isinstance(k, str) and k)
    # Only attempt form parsing when the body was NOT valid JSON (valid non-object JSON is
    # intentional — don't second-guess it) and matches the strict form shape.
    if parsed is _UNSET and _FORM_RE.match(text):
        try:
            pairs = parse_qsl(text, strict_parsing=True, keep_blank_values=True)
        except ValueError:
            return []
        return _dedupe(key for key, _value in pairs if key)
    return []


def _dedupe(names: Iterable[str]) -> list[str]:
    """Order-preserving de-dup of param names, bounded to ``_MAX_PARAM_KEYS_PER_BODY``."""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
            if len(out) >= _MAX_PARAM_KEYS_PER_BODY:
                break
    return out
