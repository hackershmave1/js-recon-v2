"""Runtime configuration, read once from the environment.

Everything tunable at the platform level lives here so no dynamic fact is
hardcoded elsewhere. Provider/LLM config is deliberately NOT here — that is
user-supplied at runtime per-run (REQ-L1) and arrives in a later slice.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECON_", env_file=".env", extra="ignore")

    env: str = "local"
    log_level: str = "INFO"

    # Absolute path to the built front-end (web/dist). When set and present, the
    # API serves the SPA (assets + client-route fallback); absent → API-only.
    # Docker sets RECON_SPA_DIST_DIR=/app/web/dist (the package is pip-installed,
    # so __file__ can't locate the repo tree there). Default suits editable/dev.
    spa_dist_dir: str | None = None

    # The app/workers connect as a NON-superuser role so row-level security is
    # actually enforced (a Postgres superuser bypasses RLS). Migrations and
    # bootstrap use the owning admin role.
    database_url: str = "postgresql+psycopg2://recon_app:recon_app@localhost:5432/recon"
    database_admin_url: str = "postgresql+psycopg2://recon:recon@localhost:5432/recon"
    redis_url: str = "redis://localhost:6379/0"

    # Largest JS upload the API will store per run. Bounds worker memory (REQ-Q5),
    # since the analyze stage reads the whole blob into memory. This is an
    # application cap, not an ingress body limit — see runs_router upload NOTE.
    # NOTE: once a run can override its fetch cap (edit-&-re-run), analyze memory is
    # bounded by max(max_upload_bytes, max_fetch_bytes_ceiling) — keep both in view.
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MiB

    # Out-of-process engines (e.g. Kingfisher). `kingfisher_bin` is the CLI name
    # or absolute path (the pinned wheel puts `kingfisher` on PATH). The timeout
    # and output cap bound a misbehaving engine (P2 sandbox, MVP level).
    kingfisher_bin: str = "kingfisher"
    sourcemapper_bin: str = "sourcemapper"
    engine_timeout_seconds: float = 120.0
    engine_max_output_bytes: int = 32 * 1024 * 1024  # 32 MiB
    # D37-L0: virtual-address-space (RLIMIT_AS) ceiling for the sourcemapper source-map
    # recovery child, applied via an exec'd `prlimit --as=<bytes>` wrapper — NOT a fork-time
    # preexec_fn, because the viewer/reveal API forks recovery from a thread pool where a
    # fork-time Python callable can deadlock before exec (CPython subprocess docs). The Go
    # recovery binary whole-loads the map (~2x its size) with no internal bound, so without
    # this a large map OOMs the box (the host/cgroup OOM-killer can reap the worker parent,
    # not just the child); the ceiling makes an over-size recovery die as a CONTAINED non-zero
    # exit -> EngineError instead. Sized from measurement against the pinned Go 1.23 binary: it
    # reserves ~0.5-0.75 GiB of virtual up front (RLIMIT_AS counts Go's PROT_NONE arenas;
    # golang/go#38010), and a 32-96 MiB map needs ~2 GiB of virtual to unmarshal — so any
    # ceiling <=1.5 GiB REGRESSES even a 32 MiB map that recovers today. 3 GiB clears the
    # measured 2 GiB floor with headroom while still tripping a genuine runaway before the
    # container mem_limit. Only sourcemapper passes it (Kingfisher is a self-contained,
    # input-capped binary); the input cap (max_source_map_bytes) is the FIRST bound, this the
    # safety net. No-op off-Linux (dev only); the Linux container enforces it. <=0 DISABLES the
    # bound (recovery runs unbounded — still cgroup-capped by the container mem_limit), per the
    # repo's "<=0 disables" convention. Env RECON_SOURCEMAPPER_MEMORY_LIMIT_BYTES.
    sourcemapper_memory_limit_bytes: int = 3 * 1024 * 1024 * 1024  # 3 GiB (RLIMIT_AS / virtual)

    # Fetch stage: pulling a target asset through the egress guard (REQ-P2).
    # D36: overall wall-clock deadline for a PRIMARY asset fetch (the run target / a crawl
    # asset). It may now exceed heartbeat_stall_threshold_seconds because the body-stream loop
    # heartbeats the job lease mid-download (fetch._fetch_hops `on_progress`) — so a large/slow
    # in-scope asset is no longer dropped at 20s. What stays < the stall window is the per-read
    # bound (fetch_read_timeout_seconds), NOT this overall deadline. Raised 20 -> 120s.
    fetch_timeout_seconds: float = 120.0
    # D36: per-READ timeout (connect / response headers / one body chunk), DECOUPLED from the
    # overall deadline above. MUST stay < heartbeat_stall_threshold_seconds: a stalled socket
    # blocks iter_bytes() unbeaten, so bounding each read is what guarantees the loop regains
    # control to heartbeat before a peer reclaims the job. A read that exceeds this raises
    # httpx.ReadTimeout, converted to a retryable per-asset failure (never an uncaught crash).
    fetch_read_timeout_seconds: float = 10.0
    # D36: overall deadline for a BEST-EFFORT SECONDARY fetch that carries only a pre-fetch beat and
    # NO mid-body heartbeat — the lazy-chunk URL fetch. Its whole deadline must stay
    # < heartbeat_stall_threshold_seconds to remain lease-safe (enforced by _check_fetch_lease_safety
    # below). Exceeding it is a soft miss (the chunk is skipped; the primary asset is unaffected).
    # NOTE: the .map fetch used to share this cap but now carries its own mid-body heartbeat and its
    # own (larger) deadline — fetch_source_map_timeout_seconds — so it is no longer bounded here.
    fetch_secondary_timeout_seconds: float = 20.0
    # D37-L2 slice 4: the external .map fetch gets its OWN deadline, decoupled from the unbeaten
    # lazy-chunk timeout above. The .map fetch now STREAMS to disk with a mid-body heartbeat
    # (fetch._make_body_beat renews the lease every heartbeat_interval_seconds), so — exactly like
    # the primary fetch_timeout_seconds — it may safely exceed heartbeat_stall_threshold_seconds
    # (hence it is deliberately NOT in _check_fetch_lease_safety's unbeaten-timeout guard). This lets
    # a big map (up to max_source_map_bytes) finish on a SLOW origin instead of soft-skipping at 20s.
    # Env RECON_FETCH_SOURCE_MAP_TIMEOUT_SECONDS.
    fetch_source_map_timeout_seconds: float = 120.0  # == fetch_timeout_seconds (both beaten)
    # Default per-fetch decoded-byte cap. Bounds worker memory (REQ-Q5 — analyze reads
    # the whole blob). A run MAY raise this via run.max_fetch_bytes (edit-&-re-run), but
    # only UP TO max_fetch_bytes_ceiling; clamp_fetch_bytes() enforces min(override-or-
    # default, ceiling) and fails closed on a non-positive override.
    max_fetch_bytes: int = 10 * 1024 * 1024  # 10 MiB — matches the upload cap
    # Hard ceiling on a per-run max_fetch_bytes override — the REAL analyze-memory bound.
    # Defaulted to the engine output cap (engine_max_output_bytes, 32 MiB): fetching more
    # than an engine can process buys nothing, and it is the size the analyze path is sized
    # to survive. Raise deliberately, with worker RAM in mind. Env RECON_MAX_FETCH_BYTES_CEILING.
    max_fetch_bytes_ceiling: int = 32 * 1024 * 1024  # 32 MiB == engine_max_output_bytes
    # D32-A1: the external source-map (.map) fetch gets its OWN byte cap, separate from
    # the bundle cap above. A real source map is 3-6x its minified bundle, so sharing the
    # bundle cap (default 10 MiB) soft-drops a large map (a 4.4 MB bundle's ~15-25 MB map)
    # and its recovered original sources are lost silently. D37-L1: raised 32 -> 96 MiB so a
    # real >32 MiB enterprise-bundle map (seen in QA) is recovered, not skipped. Safe to raise
    # ONLY because D37-L0 now bounds recovery memory (sourcemapper_memory_limit_bytes RLIMIT_AS
    # + container mem_limit): a 96 MiB map was MEASURED to recover under that 3 GiB ceiling.
    # An UNCLAMPED operator knob (no per-run override, no ceiling clamp) — raise further only
    # with the recovery memory bound in mind (child virtual peak ~2 GiB at this cap; recovered
    # output is still hard-capped at engine_max_output_bytes). Non-positive fails CLOSED: the
    # streaming cap (fetch._fetch_hops) rejects every body, so a misconfigured 0/negative
    # soft-misses each map (honest "skipped"), never an unbounded read. Env RECON_MAX_SOURCE_MAP_BYTES.
    max_source_map_bytes: int = 96 * 1024 * 1024  # 96 MiB (D37-L1; recovery mem-bounded by L0)

    # SSRF guard override — DEFAULT OFF (REQ-CE3). When true, the egress guard also
    # permits loopback + private-range targets and single-label hosts (localhost) so
    # the crawl->fetch->analyze pipeline can run against a LOCAL test target
    # (test-targets/recon-range on http://localhost:4173). Link-local / cloud-metadata
    # (169.254.169.254, fe80::/10 + their 6to4/IPv4-mapped forms) stays BLOCKED even
    # when enabled, and there is NO per-request / URL / query-param override — this
    # process-level env flag is the only switch. NEVER enable outside a developer box.
    allow_local_egress: bool = False  # env: RECON_ALLOW_LOCAL_EGRESS

    # Fetch politeness (REQ-Q3): a single target is never hammered. A run may hit
    # the same host at most once per interval (a distributed, cross-run min-gap),
    # and total outbound fetch rate is capped by a global budget. A throttled fetch
    # is rescheduled with backoff, so this bounds pressure without dropping work.
    fetch_min_host_interval_seconds: float = 1.0
    fetch_global_max_per_second: int = 10

    # Discovery/crawl stage (Slice X): headless katana crawl of an in-scope domain.
    # crawl_heartbeat_interval_seconds must stay well under
    # heartbeat_stall_threshold_seconds so the poll loop renews the job lease during
    # a long crawl and no peer worker reclaims the RUNNING job (double-crawl).
    katana_bin: str = "katana"
    # Passed to katana as `-system-chrome-path` on the headless crawl path so its
    # go-rod launcher drives this baked-in chromium (installed in the image) instead
    # of downloading its own from a CDN per container. Non-headless crawls ignore it.
    system_chrome_path: str = "/usr/bin/chromium"
    crawl_headless: bool = False
    # -jc: parse lazy/dynamic import() chunk URLs out of the JS during the crawl so
    # a standard crawl discovers webpack/vite lazy chunks (REQ-CE1). Default on;
    # config-gated kill-switch since katana flag semantics drift between releases.
    crawl_js_crawl: bool = True  # env: RECON_CRAWL_JS_CRAWL
    # REQ-CE2: on the crawl/fetch path, discover a fetched JS asset's external
    # //# sourceMappingURL=, fetch the .map through the egress guard, and link it so
    # analyze recovers original per-file sources. Best-effort — a bad/blocked map is
    # a soft miss, never fails the asset. Default on; kill-switch symmetric with -jc.
    crawl_fetch_source_maps: bool = True  # env: RECON_CRAWL_FETCH_SOURCE_MAPS
    # P4: on the fetch path, statically enumerate a webpack bundle's lazy-chunk URLs
    # (__webpack_require__.u builder, NO execution) and fetch each through the egress
    # guard so its endpoints are recovered — the runtime-computed chunk URLs katana's
    # -jc can't see. Best-effort + capped at crawl_max_assets; content-derived URLs go
    # through egress.validate_target so scope is never widened. Kill-switch symmetric
    # with -jc / source-maps. The sandboxed-exec engine for obfuscated builders is
    # deferred (DEBT D29).
    crawl_enumerate_chunks: bool = True  # env: RECON_CRAWL_ENUMERATE_CHUNKS
    crawl_depth: int = 3
    crawl_duration_seconds: float = 120.0
    # Ceiling on assets fetched + analyzed per run. This is a SECONDARY fail-closed bound: the
    # PRIMARY runaway guards are crawl_duration_seconds + crawl_max_output_bytes + the global
    # outbound fetch-rate budget, which bound the work by time and bytes regardless of the count.
    # 500 was too low for large bundle-split SPAs (a run that hits it marks DISCOVER "capped" and
    # the run "partial"), so it is raised to 2000. A genuinely larger target lifts it via env with
    # no rebuild — env: RECON_CRAWL_MAX_ASSETS. Non-positive is not special-cased; keep it > 0.
    crawl_max_assets: int = 2000
    crawl_max_output_bytes: int = 32 * 1024 * 1024  # 32 MiB
    crawl_heartbeat_interval_seconds: float = 10.0
    crawl_kill_grace_seconds: float = 15.0

    # Runtime-capture stage (REQ-P2 / SSRF): a CDP-driven headless Chromium that
    # captures EXECUTED scripts (Debugger.scriptParsed) — reaching runtime-injected,
    # inline, and eval'd JS the static fetch cannot see. DEFAULT-OFF kill switch:
    # capture drives a real browser that loads arbitrary subresources with no per-hop
    # IP pin — the same residual as the opt-in headless crawl (see recon.capture
    # docstring) — so it must be explicitly enabled. Reuses crawl_* duration /
    # heartbeat / kill_grace / max_output_bytes; per-script byte cap reuses max_fetch_bytes.
    enable_capture_mode: bool = False  # env: RECON_ENABLE_CAPTURE_MODE
    capture_nav_timeout_seconds: float = 30.0  # max wait for the initial navigation/load
    capture_idle_settle_seconds: float = 2.0  # quiet window (no new scripts) => capture done
    capture_max_scripts: int = 2000  # cap stored scripts per run (bounds worker + blob load)
    capture_max_requests: int = 1000  # REQ-C3: cap recorded XHR/fetch request URLs per run
    # Interaction driver (slice 3): after the initial load settles, drive the page —
    # autoscroll to idle, click every interactive element, and walk same-origin routes —
    # so lazily-loaded / route-split / click-gated chunks execute and get captured. All
    # bounded; capture_interact is the kill switch (off = passive slice-2 behavior).
    capture_interact: bool = True  # env: RECON_CAPTURE_INTERACT
    capture_max_scroll_steps: int = 12
    capture_max_clicks: int = 40
    capture_max_routes: int = 15

    # Object storage — blobs are referenced by key, never stored in a row (REQ-D2).
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "recon-artifacts"
    s3_region: str = "us-east-1"

    # Mounts POST /api/save-files + /api/sessions/{id}/analyze/start so the Chrome
    # extension can push captured JS into the normal run/analyze path (real S3
    # storage, worker-driven). ON by default post-cutover (Phase 4): the extension
    # is converged onto the platform, so this is a first-class capability, not a
    # spike toggle. See api/app.py + api/capture_router.py.
    enable_capture_ingest: bool = True
    capture_tenant_name: str = "capture-spike"
    # The ingest is unauthenticated (fixed capture tenant), so its state-changing
    # POSTs are Origin-locked: a request carrying a web-page Origin (http/https) is
    # rejected, closing the cross-site write vector (a browser attaches Origin to
    # cross-origin POSTs and JS cannot forge it). The extension sends
    # chrome-extension://<id> or no Origin. Kill-switch for non-browser ingest clients.
    capture_ingest_origin_lock: bool = True  # env: RECON_CAPTURE_INGEST_ORIGIN_LOCK

    # ---- User authentication (central login) ----
    # A stateless signed session token (recon.auth.token) names the logged-in user +
    # their tenant + role. EMPTY secret => auth DISABLED: /auth/login returns 503 and
    # every route falls back to the legacy X-Tenant-Id header stand-in (this is how
    # dev/test run, so the existing header-based tests need no change). Set it in any
    # real deployment to REQUIRE login. Rotating it is the "revoke all" control
    # (stateless — no per-token store).
    auth_secret: str = ""  # env: RECON_AUTH_SECRET (secret)
    auth_token_ttl_seconds: int = 8 * 3600  # login session lifetime
    # Escape hatch: honor X-Tenant-Id even when auth is ENABLED (transition/dev only).
    # DEFAULT OFF — with auth on, a signed login token is the only way in.
    allow_header_tenant: bool = False  # env: RECON_ALLOW_HEADER_TENANT
    # Capture ingest with NO valid auth session token falls back to the shared capture
    # tenant. DEFAULT ON preserves the "never drop captured JS on a typo" property and
    # keeps existing tests green; turn OFF per-deployment (e.g. the dev compose override)
    # to REJECT unauthenticated captures so post-auth JS can never leak into the shared
    # tenant.
    allow_anon_capture: bool = True  # env: RECON_ALLOW_ANON_CAPTURE

    # Login brute-force throttle (Redis-backed, POST /auth/login only —
    # recon.auth.login_rate_limit). Counts FAILED attempts per rolling window and 429s
    # BEFORE the bcrypt verify, so a login flood can't burn CPU (the no-enumeration
    # equalizer spends a bcrypt on every attempt). Keyed per-username + a global flood
    # backstop, NEVER by client IP (bare uvicorn behind an ingress proxy collapses every
    # client to one bucket = a self-DoS). Fails OPEN on a Redis error — it is defense in
    # depth, not the access gate (the password + token stay fail-closed). <=0 disables:
    # max_attempts<=0 turns the limiter off entirely; global<=0 turns off just the
    # backstop. Mirrors the <=0-disables convention of fetch/politeness. NOTE: the
    # global backstop is a conscious tradeoff — an attacker can 429 all logins for one
    # window by generating `global` failures (bounded, self-healing, fail-open).
    login_ratelimit_max_attempts: int = 10  # env: RECON_LOGIN_RATELIMIT_MAX_ATTEMPTS
    login_ratelimit_window_seconds: float = 300.0
    login_ratelimit_global_max_attempts: int = 60

    # Realtime / durability (REQ-R2, REQ-R3). heartbeat_interval_seconds also governs the D36
    # fetch body-stream beat throttle, so it is now load-bearing for lease-safety and MUST stay
    # < heartbeat_stall_threshold_seconds (enforced by _check_fetch_lease_safety below).
    heartbeat_interval_seconds: float = 5.0
    heartbeat_stall_threshold_seconds: float = 30.0
    event_stream_maxlen: int = 10_000

    # Queue retry policy (REQ-Q2).
    retry_max_attempts: int = 5
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 60.0

    # Per-asset fetch retry (DEBT D20): a transient 429/5xx on ONE crawl asset gets a
    # bounded, in-thread retry instead of dropping that asset (which turns the whole
    # run PARTIAL). Deliberately separate from the queue knobs above: this retry runs
    # INSIDE the fetch loop while holding the job lease, so its delay is capped small
    # (NOT retry_max_delay_seconds=60) and every attempt heartbeats first — together
    # that keeps the gap between lease renewals under heartbeat_stall_threshold_seconds
    # so a peer can never reclaim the job mid-retry and double-fetch. attempts=0
    # disables the retry (exactly the pre-D20 behavior); the base delay reuses
    # retry_base_delay_seconds.
    # env: RECON_FETCH_ASSET_RETRY_ATTEMPTS
    fetch_asset_retry_attempts: int = 2
    # env: RECON_FETCH_ASSET_RETRY_MAX_DELAY_SECONDS
    fetch_asset_retry_max_delay_seconds: float = 5.0

    @model_validator(mode="after")
    def _check_fetch_lease_safety(self) -> Settings:
        """Fail LOUD at startup if a fetch timing knob would break the D36 lease-safety
        invariant, rather than silently double-fetch. The fetch body stream renews the job
        lease every ``heartbeat_interval_seconds`` and each httpx read is bounded by
        ``fetch_read_timeout_seconds``; a best-effort secondary fetch has NO mid-body beat, so
        its whole ``fetch_secondary_timeout_seconds`` deadline must itself stay lease-safe. If
        ANY of the three reaches ``heartbeat_stall_threshold_seconds``, a slow/stalled fetch
        outlasts the lease and a peer worker reclaims the job (double-fetch / double-egress) —
        the exact race D36 exists to prevent, silent if only documented."""
        stall = self.heartbeat_stall_threshold_seconds
        for name, value in (
            ("heartbeat_interval_seconds", self.heartbeat_interval_seconds),
            ("fetch_read_timeout_seconds", self.fetch_read_timeout_seconds),
            ("fetch_secondary_timeout_seconds", self.fetch_secondary_timeout_seconds),
        ):
            if value >= stall:
                raise ValueError(
                    f"{name}={value} must be < heartbeat_stall_threshold_seconds={stall} "
                    "(D36 fetch lease-safety: an unbeaten fetch longer than the stall window "
                    "lets a peer reclaim the job and double-fetch)"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clamp_fetch_bytes(run_cap: int | None, settings: Settings) -> int:
    """Effective per-fetch byte cap for a run: the run's override when it is a
    positive int, else the global default — then hard-clamped to the ceiling.

    Fails CLOSED (REQ-Q5): a None / 0 / negative override falls back to the global
    default (a negative is truthy in Python, so ``run_cap or default`` alone would
    leak a negative straight through as an effectively-unbounded cap), and the
    ceiling bounds analyze memory no matter how ``run.max_fetch_bytes`` was set
    (edit-&-re-run, a future endpoint, or a direct DB write) — mirroring the egress
    guard's "fail-closed regardless of how scope_hosts was populated" posture.
    """
    base = run_cap if (run_cap is not None and run_cap > 0) else settings.max_fetch_bytes
    return min(base, settings.max_fetch_bytes_ceiling)
