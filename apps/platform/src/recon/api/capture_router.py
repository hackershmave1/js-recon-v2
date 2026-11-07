"""Extension -> platform ingest (Phase 1, flag-gated).

Accepts the Chrome extension's batched ``POST /api/save-files`` and accumulates a
capture SESSION's files into ONE platform ``Run`` (run-per-capture-session): each
batch stores its blobs to S3 and seeds pre-fetched ``run_asset`` rows; analysis is
worker-driven, triggered once by ``POST /api/sessions/{id}/analyze/start`` which
emits the ``discover.assets`` event and enqueues the DISCOVERING stage. The worker
then walks DISCOVERING (no-op: the event short-circuits the crawl) -> FETCHING
(no-op: every asset is already ``fetch_ok`` with its uploaded blob, so nothing is
egressed) -> INGESTING/CORRELATING (no-op stubs) -> ANALYZING (real) -> finalize.
Mounted only when ``settings.enable_capture_ingest`` is true (see ``api/app.py``).

Idempotency (trap T6, settled "run-per-capture-session"): a retried batch re-stores
the SAME content-addressed blob key and finds its bytes already on the url's row
(``runs/captured_versions``) — so a retry never makes a duplicate run or asset; a url
re-captured with DIFFERENT bytes gets its own ``url#sha256=`` version row. No client idempotency key, no schema
change.

Shaped by the §4 adversarial design review — two deliberate omissions on the
session-create hot path:
- NO ``engagement_id`` / ``scope_hosts`` from client metadata. An invalid
  ``projectId`` or scope host raises in ``create_session``; on this path that
  surfaces as a permanent 4xx (the extension DROPS un-recapturable JS) or a 5xx
  retry loop. Project binding + scope seeding are Phase 2 (with ``/api/projects``).
  Scope is inert here anyway: captured assets are pre-fetched, never egressed.
- Files are validated PER FILE inside the handler, not by a body-level pydantic
  model, so one malformed file can't 422 (and lose) the whole batch.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import exists, select, text, update
from sqlalchemy.exc import IntegrityError

from recon import storage
from recon.api.deps import get_redis
from recon.auth import token as auth_token
from recon.config import get_settings
from recon.db.base import admin_session, tenant_session
from recon.db.models import EngagementSession, Job, Run, RunAsset, Tenant
from recon.discover import manifest as discovery
from recon.domain import TERMINAL_STATES, AssetStatus, DiscoveryStatus, RunStage, RunState
from recon.engagements import service as engagements_service
from recon.events.log import emit, publish
from recon.observability import get_logger
from recon.runs import assets, capture_lifecycle, captured_versions, coordinator
from recon.runs import service as runs_service
from recon.sessions import service as sessions_service

log = get_logger("recon.api.capture")

router = APIRouter(prefix="/api", tags=["capture"])

# Version of the platform<->extension INGEST wire contract (server-authored, and
# distinct from the extension's own build version, which it sends as
# ``metadata.version``). Bump it when the request/response shapes below change in a
# way a client must notice. Surfaced response-side on the health handshake only —
# additive, so backward-compatible: deployed extensions read only specific response
# keys and ignore the health body, so adding a field can't break them (DEBT D8a).
CAPTURE_CONTRACT_VERSION = "1.0"


class SaveFilesIn(BaseModel):
    """Lenient by design: ``files`` is a list of raw dicts validated per-file in
    the handler (see the module docstring), never a body-level pydantic model."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] = Field(default_factory=list)


class AnalyzeStartIn(BaseModel):
    """Optional analyze/start body. ``observations`` are runtime ``{method, url}`` calls the
    extension observed at capture time (DEBT D45b1); the platform writes them as the run's
    ``capture-requests`` blob so the CORRELATING stage can promote a statically-suspected
    endpoint to confirmed. Additive + backward-compatible: a deployed client that omits the
    body (or sends only ``options``) still starts analysis with no observations."""

    options: dict[str, Any] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    # Capture lifecycle: the capture opened by capture/start. With it, Analyze does NOT end the
    # capture (capture_lifecycle.analyze); without it (an older extension), it seals as before.
    runId: str | None = None  # noqa: N815 - extension wire name


@router.get("/health")
def capture_health() -> dict:
    """The extension's workspace-client health probe (``testConnection``). Carries the
    ingest ``contractVersion`` so a client can detect wire-shape drift; the current
    extension ignores the body, so adding the field is backward-compatible."""
    return {
        "status": "ok",
        "mode": "platform",
        "contractVersion": CAPTURE_CONTRACT_VERSION,
        # The dev-only local-targets switch (RECON_ALLOW_LOCAL_EGRESS), so a client can show
        # whether `localhost` can be crawled / counted as declared scope on this server.
        "allowLocalTargets": get_settings().allow_local_egress,
    }


# --------------------------------------------------------------------------- #
# Origin-lock: the ingest is unauthenticated (a fixed capture tenant), so a page
# the operator visits could otherwise fetch() a CORS-simple text/plain POST of
# attacker-chosen "captured" JS into it (a cross-site WRITE primitive). A browser
# attaches an Origin header to every cross-origin POST and JS can neither forge nor
# suppress it (Origin is a forbidden header), so a web page cannot produce a
# no-Origin cross-site write. Rejecting a present http(s) Origin therefore closes
# the web vector; the extension's MV3 worker sends chrome-extension://<id> or no
# Origin, and curl/native clients send none — all allowed. This is independent of
# the login-token tenant routing below (_resolve_ingest_tenant).
# --------------------------------------------------------------------------- #


def _enforce_origin_lock(origin: str | None, *, enabled: bool) -> None:
    """Reject a state-changing ingest POST that carries a web-page Origin (403).

    NOTE: browsers also send Origin on SAME-origin non-GET requests. The platform
    SPA does not call these ingest routes today (it uses the operator-tenant routers),
    so locking them breaks no current caller. If the SPA ever must call one, special-
    case the platform's own origin here rather than dropping the lock.

    NOTE(DEBT): an opaque/``null`` Origin (a sandboxed iframe / ``data:`` document) has
    no http(s) scheme, so it is currently ALLOWED — a deliberate residual, because the
    MV3 worker may itself emit a ``null`` Origin and we won't risk rejecting real
    capture. Its blast radius is bounded to the SHARED ``capture-spike`` tenant: a
    logged-in operator's real captures are re-homed by their auth session token into
    their OWN tenant (``_resolve_ingest_tenant``), so a ``null``-Origin write can only
    land fake findings / DoS in the throwaway shared tenant, never an operator's.
    Tracked in DEBT.md; revisit rejecting ``null`` once the extension worker's actual
    Origin is confirmed in a live browser.
    """
    if not enabled:
        return
    # Via FastAPI, `origin` is a str (header present) or None (absent). A direct
    # in-process call (the concurrency tests invoke the handler without FastAPI
    # resolving the Header default) leaves the Header sentinel object — treat any
    # non-str as "no Origin" so the guard never crashes on it.
    if isinstance(origin, str) and urlsplit(origin).scheme in ("http", "https"):
        raise HTTPException(status_code=403, detail="cross-site origin not allowed")


# --------------------------------------------------------------------------- #
# Tenant + session resolution (single capture tenant; no X-Tenant-Id header).
# --------------------------------------------------------------------------- #


def _get_or_create_tenant(name: str) -> str:
    """The single capture tenant, keyed on the fixed ``capture_tenant_name``.

    ``tenant.name`` intentionally carries no unique constraint (the multi-tenant
    platform allows duplicate display names), so a first-ever *concurrent* bootstrap
    could otherwise insert two capture tenants and partition sessions across them
    (DEBT D1). Fast path: a plain SELECT (the tenant already exists on all but the
    first request, so no lock on the hot path). On a miss, serialize the create with
    a transaction-scoped advisory lock keyed on the name and re-check *under* it, so
    two racing bootstraps can't both insert. Lock + recheck + INSERT live in ONE
    admin transaction (``create_tenant`` isn't reused — it would open its own
    session and the lock would guard nothing); the xact lock auto-releases at commit.
    """
    with admin_session() as session:
        existing = session.scalar(select(Tenant.id).where(Tenant.name == name))
        if existing is not None:
            return str(existing)
    with admin_session() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": name})
        existing = session.scalar(select(Tenant.id).where(Tenant.name == name))
        if existing is not None:
            return str(existing)
        tenant = Tenant(name=name)
        session.add(tenant)
        session.flush()
        return str(tenant.id)


def _bearer_token(authorization: str | None) -> str | None:
    """The token from an ``Authorization: Bearer <token>`` header, else ``None``. Tolerates
    a non-str (a direct in-process call leaves FastAPI's Header sentinel, not a header)."""
    if not isinstance(authorization, str):
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _resolve_ingest_tenant(authorization: str | None) -> tuple[str, bool]:
    """Resolve which tenant an ingest request writes into, and whether it routed to a
    logged-in operator's tenant (the ``paired`` flag in the response).

    The tenant is derived ONLY from a server-signed auth SESSION token (recon.auth.token),
    never a client value: a valid ``typ=auth`` token names the operator tenant and returns
    ``True``.

    With no valid token: fall back to the shared capture tenant when ``allow_anon_capture``
    is on (the default — preserves "never drop captured JS on a typo"), else REJECT with
    401 so post-auth JS can never leak into the shared tenant. Still fails CLOSED — a bad
    token never errors open into an operator tenant."""
    settings = get_settings()
    token = _bearer_token(authorization)
    if token:
        claims = auth_token.verify(token, key=settings.auth_secret)
        if claims is not None:
            return claims.tenant_id, True
    # Anon fallback exists only in unauthenticated mode. Once real login is configured
    # (auth_secret set), a tokenless OR unrecognized-token capture is NEVER accepted (fail
    # closed) regardless of allow_anon_capture — so enabling auth can't accidentally leave
    # the shared-tenant leak open (adversarial review Finding 5).
    if settings.auth_secret or not settings.allow_anon_capture:
        raise HTTPException(status_code=401, detail="capture requires a valid login token")
    return _get_or_create_tenant(settings.capture_tenant_name), False


def _find_session_by_external_id(tenant_id: str, ext_session_id: str) -> str | None:
    with tenant_session(tenant_id) as session:
        row_id = session.scalar(
            select(EngagementSession.id).where(EngagementSession.external_id == ext_session_id)
        )
        return str(row_id) if row_id is not None else None


def _safe_uuid(value: Any) -> str | None:
    """Canonical UUID string, or None for a falsy/malformed value — so a bad
    ``projectId`` can never reach a DB lookup that would raise (StatementError)."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _create_capture_session(
    tenant_id: str,
    ext_session_id: str,
    engagement_id: str | None,
    scope_hosts: list[str] | None = None,
) -> str:
    scope_hosts = sessions_service.capture_scope_hosts(scope_hosts or [])
    # scope_hosts seeds the Hosts-tab in/out-of-scope classification: captured assets
    # never egress so the security guard is inert here (§4 defect B), but the operator's
    # declared domains matter for display. capture_scope_hosts leaves out entries the server
    # can't accept (localhost with local targets disabled, an IP literal) — passing them through
    # made create_session raise SessionInvalid on BOTH attempts below, a 500 the extension
    # retried forever (DEBT D71). Bind the engagement (projectId) if it resolves cleanly, but NEVER raise on the
    # ingest hot path (§4 defect A): a foreign/deleted engagement makes create_session
    # raise SessionInvalid *before* any row is added, so we retry unbound. A malformed
    # id was already filtered to None by _safe_uuid upstream. external_id is the
    # idempotency key (DEBT D1) and carries the ext UUID; name is left NULL — a capture
    # session has no user label, and its Sessions-card host is derived from its captured
    # assets at read time (sessions.service._summary/_card_label), never the raw UUID.
    try:
        view = sessions_service.create_session(
            tenant_id,
            name=None,
            external_id=ext_session_id,
            scope_hosts=scope_hosts or [],
            authorized_by="chrome-extension-capture",
            engagement_id=engagement_id,
        )
    except sessions_service.SessionInvalid:
        log.warning(
            "capture.session.engagement_ignored",
            session=ext_session_id,
            engagement_id=engagement_id,
        )
        view = sessions_service.create_session(
            tenant_id,
            name=None,
            external_id=ext_session_id,
            scope_hosts=scope_hosts or [],
            authorized_by="chrome-extension-capture",
            engagement_id=None,
        )
    return view.id


def _get_or_create_session(
    tenant_id: str,
    ext_session_id: str,
    engagement_id: str | None = None,
    root_domains: list[str] | None = None,
) -> str:
    # Map the extension's sessionId -> a platform session idempotently, keyed by
    # external_id (UNIQUE(tenant_id, external_id)), so a retried OR concurrent batch
    # reuses the session instead of piling up duplicates (DEBT D1).
    existing = _find_session_by_external_id(tenant_id, ext_session_id)
    if existing is not None:
        # Best-effort: backfill scope on sessions created before scope seeding was added.
        # Silently drops invalid entries; never overwrites an existing scope.
        if root_domains:
            sessions_service.seed_scope_hosts_if_empty(tenant_id, existing, root_domains)
        return existing
    try:
        return _create_capture_session(tenant_id, ext_session_id, engagement_id, root_domains)
    except IntegrityError:
        # Lost the create race to a concurrent batch for the same sessionId — the
        # unique key rejected the duplicate. Re-select the winner (committed by now at
        # READ COMMITTED; same tenant GUC, so RLS matches — never None here).
        resolved = _find_session_by_external_id(tenant_id, ext_session_id)
        if resolved is None:
            raise
        return resolved


# --------------------------------------------------------------------------- #
# Accumulating run: one open Run per capture session, appended to across batches.
# --------------------------------------------------------------------------- #


def _find_open_capture_run(tenant_id: str, ext_session_id: str) -> str | None:
    """The session's OPEN capture accumulator run, identified by the
    ``capture_external_id`` marker (UNIQUE(tenant_id, capture_external_id)).
    analyze/start nulls the marker to seal a round, so a sealed run is excluded here
    and the next batch opens a fresh one."""
    with tenant_session(tenant_id) as session:
        run_id = session.scalar(select(Run.id).where(Run.capture_external_id == ext_session_id))
        return str(run_id) if run_id is not None else None


def _accumulating_run_id(tenant_id: str, session_id: str, ext_session_id: str, redis: Redis) -> str:
    """The session's open run to append this batch to, keyed on the
    ``capture_external_id`` marker so a retried OR concurrent first batch can't open
    duplicate rounds (DEBT D1): the loser's ``create_run`` hits the unique key and
    self-heals to the winner. Sealed by analyze/start nulling the marker, so the next
    batch opens a fresh run (a new capture round). ``target`` stays None: an upload
    run must never be crawled/fetched — the discover/fetch stages no-op on a
    target-less, pre-fetched run (``crawl.py:45`` / ``fetch.py:171``)."""
    existing = _find_open_capture_run(tenant_id, ext_session_id)
    if existing is not None:
        return existing
    try:
        view = runs_service.create_run(
            redis,
            tenant_id=tenant_id,
            session_id=str(session_id),
            target=None,
            capture_external_id=ext_session_id,
        )
        return view.id
    except IntegrityError:
        # Lost the open-round create race — the concurrent batch created it; re-select.
        resolved = _find_open_capture_run(tenant_id, ext_session_id)
        if resolved is None:
            raise
        return resolved


def _valid_file(f: dict, max_bytes: int) -> tuple[str, bytes] | None:
    """``(url, content_bytes)`` for a well-formed, within-cap file, else ``None``
    (a per-file failure — never a batch-wide 422). The cap bounds worker memory
    (REQ-Q5): the analyze stage reads the whole blob in."""
    url = f.get("url")
    content = f.get("content")
    if not isinstance(url, str) or not url or not isinstance(content, str):
        return None
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        return None
    return url, data


def _valid_source_map(f: dict, max_bytes: int) -> tuple[bytes | None, bool]:
    """Serialized source-map bytes for a file that carries one within the cap.

    Returns ``(data, False)`` for a valid in-cap map, ``(None, True)`` when a map is
    present but oversized (D32-A2: the caller sets ``source_map_skipped`` so analyze
    reports coverage ``"skipped"`` rather than ``"none"``), and ``(None, False)`` for
    absent or malformed (no honesty signal — there was nothing to recover).

    The extension sends ``sourceMapContent`` as an already-parsed JSON OBJECT — it
    only sets it after a successful client-side ``JSON.parse`` (``background.js``), so
    what arrives is null or an object. A missing / non-object / oversized map is
    skipped WITHOUT failing the file: the JS still analyzes, and a bad map is
    tolerated again at analyze time (the "capture" source-map origin).

    D17 blast-radius note (untrusted, shared-tenant ingress): ``max_bytes`` here is
    ``max_source_map_bytes`` (96 MiB since D37-L1), larger than the 10 MiB it used to share
    with the JS-content cap. This does NOT widen peak parse-time memory — Starlette has already
    parsed the whole request body into ``smc`` (a dict) before this runs, so that
    allocation is spent regardless of the cap (a pre-existing, uncapped-at-app-layer
    property of this path; a hard body limit belongs at the ingress/reverse-proxy).
    The cap raise only permits a larger ``json.dumps`` re-serialization + blob PUT — a
    bounded, transient <=32 MiB bytestring per accepted map, freed after storage; the
    analyze-memory bound is still enforced downstream (``engine_max_output_bytes``)."""
    smc = f.get("sourceMapContent")
    if not isinstance(smc, dict):
        return None, False
    try:
        data = json.dumps(smc).encode("utf-8")
    except (TypeError, ValueError):
        return None, False
    if len(data) > max_bytes:
        # D32-A2: map was present but over the cap — caller sets source_map_skipped
        # so analyze reports coverage:"skipped" instead of the silent "none".
        return None, True
    return data, False


class _CaptureEnded(Exception):
    """Raised inside a batch's transaction when its capture closed meanwhile (rolls it back)."""


def _ended_capture_batch(
    tenant_id: str, run_id: str, payload: SaveFilesIn, paired: bool, session_id: str
) -> dict:
    """A batch for a capture that has ended. A retry of files already stored in it (the extension
    re-sends a batch whose ack it never saw) is a harmless no-op; anything else is a non-retryable
    409, never a new run (the stray one-file run of the old seal-on-analyze path). The answer is
    the same 409 for another session's run, so a batch can't probe what a run holds."""
    max_bytes = get_settings().max_upload_bytes
    parsed = [_valid_file(f, max_bytes) for f in payload.files]
    wanted = {
        (url, storage.object_key(tenant_id, run_id, "input", data))
        for url, data in (p for p in parsed if p is not None)
    }
    with tenant_session(tenant_id) as session:
        owner = session.scalar(select(Run.session_id).where(Run.id == run_id))
        stored = set(
            session.execute(
                select(RunAsset.url, RunAsset.input_ref).where(RunAsset.run_id == run_id)
            ).tuples()
        )
    if str(owner) != str(session_id) or not wanted or None in parsed or wanted - stored:
        raise HTTPException(status_code=409, detail="capture_ended")
    return {
        "success": True,
        "paired": paired,
        "sessionId": session_id,
        "runId": run_id,
        "stored": 0,
        "failed": 0,
        "files": [
            {
                "url": f.get("url"),
                "contentHash": f.get("contentHash"),
                "stored": False,
                "error": "already stored",
            }
            for f in payload.files
        ],
    }


def _seed_fetched_assets(
    tenant_id: str,
    run_id: str,
    uploads: list[tuple[captured_versions.CapturedAsset, dict]],
    max_versions: int,
    *,
    open_capture_of: str | None = None,
) -> tuple[int, int]:
    """Seed this batch's stored files as ``run_asset`` rows in ONE transaction — every
    version of a re-captured url kept (``assets.add_captured_versions``) — and mark each file
    turned away (the per-url version cap, or its row deleted mid-batch) ``stored: false``.

    Returns ``(the run's cumulative asset count, version rows added)``. The count is the
    ABSOLUTE value the slice-2 ``capture.received`` indicator reports (eventually-consistent
    under concurrent same-session batches; the UI reducer keeps the max)."""
    with tenant_session(tenant_id) as session:
        # A named capture (runId) must still be open when its rows commit: End or Cancel may have
        # sealed it while this batch was uploading blobs. Checked under a share lock in THIS
        # transaction, so the batch either lands in an open capture or not at all.
        if open_capture_of is not None and not capture_lifecycle.accept_batch(
            session, run_id, open_capture_of
        ):
            raise _CaptureEnded(run_id)
        added = captured_versions.add_captured_versions(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            files=[asset for asset, _ in uploads],
            max_versions=max_versions,
        )
        # Same transaction as the rows: an analyzed capture's next pass is scheduled atomically
        # with the files it must cover (a crash between two commits would strand them).
        capture_lifecycle.note_batch(session, run_id)
    for url in added.versions:
        log.info("capture.asset_version_added", run_id=run_id, url=url)
    for asset, result in uploads:
        if asset in added.capped:
            result.update(stored=False, error="too many versions of this url")
            log.warning("capture.asset_version_capped", run_id=run_id, url=asset.url)
        elif asset in added.vanished:
            result.update(stored=False, error="asset removed during upload")
            log.warning("capture.asset_vanished", run_id=run_id, url=asset.url)
    return added.total, len(added.versions)


@router.post("/save-files")
def save_files(
    payload: SaveFilesIn,
    origin: str | None = Header(default=None, alias="Origin"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    settings = get_settings()
    _enforce_origin_lock(origin, enabled=settings.capture_ingest_origin_lock)
    redis = get_redis()
    meta = payload.metadata or {}
    ext_session_id = meta.get("sessionId") or (
        payload.files[0].get("sessionId") if payload.files else None
    )
    # The Bearer login token (if any) selects the operator tenant; else capture-spike.
    tenant_id, paired = _resolve_ingest_tenant(authorization)
    if not ext_session_id:
        return {
            "success": True,
            "paired": paired,
            "sessionId": None,
            "runId": None,
            "stored": 0,
            "failed": 0,
            "files": [],
        }
    engagement_id = _safe_uuid(
        meta.get("projectId")
    )  # bind the project if it resolves; else unbound
    # rootDomains from scopeMetadata(): the extension's declared capture scope.
    # Used for display-only Hosts in/out-of-scope classification; never for egress.
    raw_domains = meta.get("rootDomains")
    root_domains = (
        [str(d) for d in raw_domains if isinstance(d, str) and d.strip()]
        if isinstance(raw_domains, list)
        else []
    )
    session_id = _get_or_create_session(tenant_id, ext_session_id, engagement_id, root_domains)
    explicit_run = _safe_uuid(meta.get("runId"))
    if explicit_run is not None:
        # Capture lifecycle: the batch names its capture. Only the session's OPEN capture takes
        # files; a late batch for an ended one never opens a stray run (see _ended_capture_batch).
        if capture_lifecycle.find_open_run(tenant_id, ext_session_id) != explicit_run:
            return _ended_capture_batch(tenant_id, explicit_run, payload, paired, session_id)
        run_id = explicit_run
    else:
        run_id = _accumulating_run_id(tenant_id, session_id, ext_session_id, redis)

    file_results: list[dict] = []
    uploads: list[tuple[captured_versions.CapturedAsset, dict]] = []
    seen: set[tuple[str, str]] = set()  # (url, content sha) already in this batch
    failed = 0
    total_bytes = 0
    # Timing observability (§5): the batch is stored synchronously and the response
    # ack (200) is durable-before-return — so this bounds how close a batch runs to
    # the extension's 30s upload AbortController. A batch trending toward that ceiling
    # is the tripwire to revisit parallelizing the puts (rejected now as YAGNI: batch
    # <=5, and Phase-1 idempotency already makes a timeout+retry harmless).
    store_started = time.monotonic()
    for f in payload.files:
        content_hash = f.get("contentHash")
        parsed = _valid_file(f, settings.max_upload_bytes)
        if parsed is None:
            failed += 1
            file_results.append(
                {
                    "url": f.get("url"),
                    "contentHash": content_hash,
                    "stored": False,
                    "error": "invalid or oversized file",
                }
            )
            continue
        url, data = parsed
        digest = hashlib.sha256(data).hexdigest()
        if (url, digest) in seen:
            # The same file twice in one batch adds nothing (no double count). The same url
            # with different bytes is another version, kept by _seed_fetched_assets.
            file_results.append(
                {
                    "url": url,
                    "contentHash": content_hash,
                    "stored": False,
                    "error": "duplicate file in batch",
                }
            )
            continue
        seen.add((url, digest))
        map_key = None
        try:
            key = storage.put_blob(tenant_id, run_id, "input", data)
            # Store the captured source map alongside the JS. A storage infra failure
            # here is STILL a 503 so the whole idempotent batch retries (a retry
            # re-stores the same content-addressed blobs). A missing/malformed map is
            # simply absent (None) — it never fails the file (a bad map is tolerated
            # again at analyze time via the "capture" origin). D32-A2: the map gets its
            # OWN (larger) cap — a real source map is 3-6x its bundle — and an OVERSIZED
            # map is recorded as skipped (honest coverage), not silently dropped.
            map_bytes, map_skipped = _valid_source_map(f, settings.max_source_map_bytes)
            if map_bytes is not None:
                map_key = storage.put_blob(tenant_id, run_id, "source_map", map_bytes)
                total_bytes += len(map_bytes)
            elif map_skipped:
                # D32-A2 honesty: a present-but-oversized map is a real coverage gap
                # (analyze -> source_map:"skipped"), not "no map". Flagged on the
                # asset's row below (same tx as the fetch_ok seed).
                log.info(
                    "capture.source_map_skipped",
                    run_id=run_id,
                    url=url,
                    reason="oversized",
                )
        except Exception as exc:  # infra: 5xx so the extension RETRIES the whole (idempotent) batch
            log.error("capture.save_files.blob_failed", url=url, error=str(exc))
            raise HTTPException(status_code=503, detail="blob storage unavailable") from exc
        total_bytes += len(data)
        result = {"url": url, "contentHash": content_hash, "runId": run_id, "stored": True}
        file_results.append(result)
        uploads.append(
            (
                captured_versions.CapturedAsset(
                    url=url, input_ref=key, source_map_ref=map_key, source_map_skipped=map_skipped
                ),
                result,
            )
        )

    stored = added_versions = 0
    if uploads:
        try:
            total, added_versions = _seed_fetched_assets(
                tenant_id,
                run_id,
                uploads,
                settings.capture_max_versions_per_url,
                open_capture_of=ext_session_id if explicit_run is not None else None,
            )
        except _CaptureEnded:
            return _ended_capture_batch(tenant_id, run_id, payload, paired, session_id)
        stored = sum(1 for _, result in uploads if result["stored"])
        # Slice 2 — tell the run workspace captures are arriving, LIVE. The batch is
        # already durably stored+committed above, so this capture.received event is a
        # BEST-EFFORT side-channel: an event-bus hiccup (or a malformed url below) must
        # never turn a durable capture into a 5xx (the extension would retry forever, or
        # DROP the JS on a 4xx). It carries the run's cumulative asset count as an ABSOLUTE
        # value, not a per-batch delta, so the SSE-replayed reducer stays last-writes-win
        # and never double-counts on reconnect (under concurrent same-session batches the
        # count is eventually-consistent, not instantaneous — the reducer keeps the max,
        # and captured assets are insert-only). Visible only once captures are re-homed into
        # the operator tenant (paired): the per-run SSE stream is tenant-gated, so on
        # capture-spike nobody is subscribed. The chip may briefly linger after analyze/start
        # until the worker advances the run off QUEUED — benign, the run is already sealed.
        try:
            # urlsplit(...).hostname raises on a malformed url (e.g. an unterminated IPv6
            # bracket); keep it INSIDE the guard so a durably-stored batch never 5xxs.
            last_host = urlsplit(uploads[-1][0].url).hostname
            emit(
                redis,
                tenant_id=tenant_id,
                run_id=run_id,
                event_type="capture.received",
                payload={
                    "stored": stored,
                    "total": total,
                    "last_host": last_host,
                    "ts": int(time.time()),
                },
            )
        except Exception as exc:  # never fail a durable batch on an observability emit
            log.warning("capture.received.emit_failed", run_id=run_id, error=str(exc))

    log.info(
        "capture.save_files",
        session=ext_session_id,
        run_id=run_id,
        stored=stored,
        versions=added_versions,
        failed=failed,
        bytes=total_bytes,
        duration_ms=round((time.monotonic() - store_started) * 1000),
    )
    return {
        "success": True,
        "paired": paired,
        "sessionId": session_id,
        "runId": run_id,
        "stored": stored,
        "failed": failed,
        "files": file_results,
    }


# --------------------------------------------------------------------------- #
# analyze/start: emit discover.assets + enqueue the worker once, on the session's
# latest run (the one /save-files accumulated into).
# --------------------------------------------------------------------------- #


def _latest_run_id(tenant_id: str, session_id: str) -> str | None:
    with tenant_session(tenant_id) as session:
        run_id = session.scalar(
            select(Run.id)
            .where(Run.session_id == str(session_id))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        return str(run_id) if run_id is not None else None


def _run_has_job(tenant_id: str, run_id: str) -> bool:
    with tenant_session(tenant_id) as session:
        return bool(session.scalar(select(exists(select(Job.id).where(Job.run_id == str(run_id))))))


def _manifest_domain(rows: list, fallback: str) -> str:
    for row in rows:
        host = urlsplit(row.url).hostname
        if host:
            return host
    return fallback


def _capped_body(value: Any, max_bytes: int) -> str | None:
    """A request/response body re-capped to ``max_bytes`` UTF-8 bytes (D45b2), or ``None`` for a
    missing / non-str / empty value. NEVER trust the client's own cap — a forged oversized body is
    TRUNCATED here (``decode("utf-8", "ignore")`` drops a split trailing codepoint), so it can
    neither blow up the ``capture-requests`` blob nor the downstream Kingfisher body-scan. A hard,
    fail-closed byte bound; a body under the cap round-trips unchanged."""
    if not isinstance(value, str) or not value:
        return None
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", "ignore")


def _normalize_observations(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Validate + normalize client-supplied runtime observations (DEBT D45b1/b2) to the shape the
    correlate matcher expects: ``[{"method","url"}]`` with ``url`` = ``scheme://host/path``
    (query/fragment dropped), deduped and capped. Never trust the client — re-validate the
    scheme + host here and drop anything malformed (fail-closed: a bad item is skipped, never
    the whole batch).

    D45b2 (additive): an observation MAY also carry ``reqBody``/``respBody`` text; when present +
    a ``str``, each is preserved on the entry re-capped SERVER-SIDE (``_capped_body``) so the
    CORRELATING stage can secret-scan the payload + extract light param hints. The ``{method,url}``
    correlate matcher (``recon.correlate.match.correlate``) reads only method + url, so these extra
    keys ride along untouched. Bodies do NOT participate in the dedup key — the first sighting of a
    ``(method, url)`` wins, carrying its own bodies."""
    settings = get_settings()
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    cap = settings.capture_max_requests
    for item in raw:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").upper().strip()
        url = str(item.get("url") or "").strip()
        if not method or not url:
            continue
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        norm = f"{parts.scheme}://{parts.netloc}{parts.path}"
        key = f"{method} {norm}"
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, str] = {"method": method, "url": norm}
        req_body = _capped_body(item.get("reqBody"), settings.capture_max_request_body_bytes)
        if req_body:
            entry["reqBody"] = req_body
        resp_body = _capped_body(item.get("respBody"), settings.capture_max_response_body_bytes)
        if resp_body:
            entry["respBody"] = resp_body
        out.append(entry)
        if len(out) >= cap:
            break
    return out


@router.post("/sessions/{ext_session_id}/analyze/start")
def analyze_start(
    ext_session_id: str,
    body: AnalyzeStartIn | None = None,
    origin: str | None = Header(default=None, alias="Origin"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    redis = get_redis()
    settings = get_settings()
    _enforce_origin_lock(origin, enabled=settings.capture_ingest_origin_lock)
    tenant_id, _ = _resolve_ingest_tenant(authorization)
    session_id = _find_session_by_external_id(tenant_id, ext_session_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="unknown capture session")
    if body is not None and body.runId:
        from recon.api.capture_lifecycle_router import run_of_session  # avoid an import cycle

        run_id = run_of_session(tenant_id, ext_session_id, body.runId)
        observed = _normalize_observations(body.observations) if body.observations else []
        return capture_lifecycle.analyze(redis, tenant_id, run_id, observations=observed)

    run_id = _latest_run_id(tenant_id, session_id)
    if run_id is None:
        return {"started": False, "message": "no run to analyze"}
    rows = assets.list_for_run(tenant_id, run_id)
    if not rows:
        return {"started": False, "message": "no captured files to analyze"}
    if _run_has_job(tenant_id, run_id):
        # Already enqueued (idempotent: a retried analyze/start, or the run is a
        # completed prior round). Do not enqueue a second walk.
        return {"started": True, "message": "analysis already started", "runId": run_id}

    # Store the manifest (so GET /runs/{id}/assets reads back, trap T5) and the runtime request
    # observations (DEBT D45b1: CORRELATING promotes matching suspected endpoints to confirmed;
    # none from an older client, and correlate then no-ops) OUTSIDE the seal transaction below,
    # so the run-row lock never spans an S3 round-trip. On the rare concurrent-loser path this is
    # a harmless content-addressed re-write of identical bytes (do NOT move it into the
    # winner-only branch).
    observed = _normalize_observations(body.observations) if body and body.observations else []
    found = discovery.store_manifest(
        tenant_id=tenant_id,
        run_id=run_id,
        domain=_manifest_domain(rows, ext_session_id),
        status=DiscoveryStatus.OK,
        asset_urls=[r.url for r in rows],
        source="extension",
        observed_requests=observed or None,
    )
    # ONE transaction: seal the run, record discover.assets, and insert the Job — all
    # atomic. The seal is a GUARDED update (DEBT D14): null the capture accumulator
    # marker only while it is still set, and let the rowcount elect a single winner.
    # Two concurrent analyze/start calls both clear the _run_has_job fast-path gate
    # above, then race here; under READ COMMITTED the loser blocks on the run row lock,
    # re-evaluates the predicate against the winner's committed row (marker now NULL),
    # matches 0 rows, and returns idempotent WITHOUT enqueuing a second DISCOVERING
    # walk (same guarded-UPDATE idiom as runs/service._apply_transition). Sealing stays
    # atomic WITH the Job insert — the D1 invariant "a capture run has a Job <=> its
    # marker is NULL": were the marker nulled in a separate earlier commit and the Job
    # insert then failed, the run would be sealed-but-jobless (invisible to the
    # accumulator, the progress reader, and this endpoint's own guard) and a concurrent
    # batch would open a new run, orphaning this run's captured post-auth JS. The Redis
    # enqueue + event publish are the post-commit outbox step (publish_stage_job).
    with tenant_session(tenant_id) as session:
        sealed = session.execute(
            update(Run)
            .where(Run.id == run_id, Run.capture_external_id.is_not(None))
            .values(capture_external_id=None)
        )
        if sealed.rowcount != 1:
            return {"started": True, "message": "analysis already started", "runId": run_id}
        event = discovery.record(session, tenant_id=tenant_id, run_id=run_id, found=found)
        job_id, job_message = coordinator.create_stage_job(
            session, tenant_id=tenant_id, run_id=run_id, stage=RunStage.DISCOVERING
        )
    publish(redis, event)  # after commit — a subscriber must never see an unpersisted event
    coordinator.publish_stage_job(redis, job_message)
    log.info(
        "capture.analyze_start", session=ext_session_id, run_id=run_id, count=len(rows), job=job_id
    )
    return {"started": True, "job": job_id, "runId": run_id}


# --------------------------------------------------------------------------- #
# analyze/progress: adapt the run's per-asset status into the extension popup's
# `job` shape (counts + per-file url/status). See workspace-client.getAnalysisProgress.
# --------------------------------------------------------------------------- #


def _latest_analyzed_run(tenant_id: str, session_id: str) -> tuple[str, str] | None:
    """The session's latest run that has been ENQUEUED for analysis (has a Job),
    with its state. A never-analyzed accumulating run (QUEUED, no Job) is excluded
    on purpose: the popup would read pending assets as "running" and disable the
    Analyze button (§4). Excluding it makes progress report idle there instead, and
    preferring the latest enqueued run keeps a finished round visible after the next
    capture round opens a fresh accumulating run."""
    with tenant_session(tenant_id) as session:
        row = session.execute(
            select(Run.id, Run.state)
            .where(
                Run.session_id == str(session_id),
                exists(select(Job.id).where(Job.run_id == Run.id)),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        ).first()
        return (str(row[0]), row[1]) if row is not None else None


def _asset_progress_status(row: assets.AssetRow, *, run_analyzing: bool, run_terminal: bool) -> str:
    """Map a capture asset to the popup's per-file vocabulary. Captured assets are
    pre-fetch_ok, so the signal is analyze_status; a fetch failure still surfaces.
    A still-``pending`` asset on a TERMINAL run (abnormal termination: analyze
    retries exhausted -> FAILED, or CANCELLED) settles to ``failed`` so the popup's
    ``inFlight = queued + analyzing`` reaches 0 instead of polling "running" forever."""
    if row.analyze_status == AssetStatus.OK.value:
        return "completed"
    if (
        row.analyze_status == AssetStatus.FAILED.value
        or row.fetch_status == AssetStatus.FAILED.value
    ):
        return "failed"
    if run_terminal:
        return "failed"
    return "analyzing" if run_analyzing else "queued"


def _idle_job() -> dict:
    return {
        "counts": {
            "queued": 0,
            "analyzing": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "total": 0,
        },
        "files": [],
    }


@router.get("/sessions/{ext_session_id}/analyze/progress")
def analyze_progress(
    ext_session_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    tenant_id, _ = _resolve_ingest_tenant(authorization)
    session_id = _find_session_by_external_id(tenant_id, ext_session_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="unknown capture session")

    latest = _latest_analyzed_run(tenant_id, session_id)
    if latest is None:
        return {"success": True, "sessionId": session_id, "job": _idle_job()}
    run_id, state = latest
    run_state = RunState(state)
    run_analyzing = run_state == RunState.ANALYZING
    run_terminal = run_state in TERMINAL_STATES
    rows = assets.list_for_run(tenant_id, run_id)
    counts = {
        "queued": 0,
        "analyzing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "total": len(rows),
    }
    files = []
    for row in rows:
        status = _asset_progress_status(row, run_analyzing=run_analyzing, run_terminal=run_terminal)
        counts[status] += 1
        files.append({"url": row.url, "status": status})
    return {"success": True, "sessionId": session_id, "job": {"counts": counts, "files": files}}


# --------------------------------------------------------------------------- #
# projects <-> engagements: the extension's project = a v2 engagement. GET must be
# a BARE ARRAY (the extension does Array.isArray(body)?body:[]); the project id is
# `id` (not engagement_id); a `defaults` config doc is synthesized from the
# engagement's scope + v1 system defaults (the extension only reads scope + creates
# name+rootDomains, so nothing it uses is lost).
# --------------------------------------------------------------------------- #


def _engagement_to_project(view: engagements_service.EngagementView) -> dict:
    return {
        "id": view.id,
        "name": view.name,
        "createdAt": view.created_at,
        "updatedAt": view.updated_at,
        "defaults": {
            "scope": {"rootDomains": list(view.in_scope_domains), "includeSubdomains": True},
            "capture": {"outOfScopeMode": "tag", "maxAssetMb": 10},
            "denylist": {"rules": [], "useDefaultProfile": True},
            "analysis": {"analyzeOnUpload": False, "captureSourceMaps": True},
        },
    }


@router.get("/projects")
def list_projects(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> list[dict]:
    tenant_id, _ = _resolve_ingest_tenant(authorization)
    return [_engagement_to_project(v) for v in engagements_service.list_engagements(tenant_id)]


@router.post("/projects")
def create_project(
    payload: dict[str, Any],
    origin: str | None = Header(default=None, alias="Origin"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    settings = get_settings()
    _enforce_origin_lock(origin, enabled=settings.capture_ingest_origin_lock)
    tenant_id, _ = _resolve_ingest_tenant(authorization)
    name = str(payload.get("name") or "").strip()
    if not name:
        # create is user-initiated (not the JS-loss ingest path); a string detail
        # matches the platform's engagements 400 and renders cleanly in the popup.
        raise HTTPException(status_code=400, detail="a project name is required")
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    scope = defaults.get("scope") if isinstance(defaults.get("scope"), dict) else {}
    raw_domains = scope.get("rootDomains")
    root_domains = [str(d) for d in raw_domains] if isinstance(raw_domains, list) else []
    view = engagements_service.create_engagement(
        tenant_id, name=name, in_scope_domains=root_domains, out_of_scope_domains=[]
    )
    return _engagement_to_project(view)
