# Vespasian Discovery Engine — Design Spec

**Date:** 2026-04-15  
**Status:** Approved  
**Goal:** Add Vespasian as a 4th discovery engine option in the Create Session flow. When selected, Katana runs concurrently with a Vespasian scan. Katana feeds the existing JS ingestion and analysis pipeline (unchanged). Vespasian produces an OpenAPI 3.0 YAML spec stored as a session artifact and downloadable from the session card.

---

## Background & Motivation

### Current State

The Create Session flow supports three discovery engines:

| Engine | How it works |
|--------|-------------|
| `headless` | Playwright Chromium intercepts script responses |
| `katana` | Katana CLI crawls the target, emits JSONL of discovered URLs |
| `hybrid` | Both engines run sequentially; results are combined |

All three engines produce a list of JS file URLs. Those URLs are fetched, ingested as `File` records, and fed into the JS analysis pipeline (jsluice + REP extractors + source map processor).

The pipeline extracts endpoints and secrets from the JS files, but the output is a flat list of URLs with limited structure — HTTP methods are often absent, request/response body schemas are not captured, and path parameters are not modelled.

### Why Vespasian

[Vespasian](https://github.com/praetorian-inc/vespasian) is a Go tool that observes real HTTP traffic (via headless browser crawling, or imported from Burp/HAR/mitmproxy) and generates structured API specifications: OpenAPI 3.0 for REST, GraphQL SDL, or WSDL.

Internally, Vespasian uses [Katana](https://github.com/projectdiscovery/katana) as its headless crawl engine. It intercepts every HTTP request the target app's JavaScript makes at runtime, classifies those requests (REST / GraphQL / SOAP), probes discovered endpoints (OPTIONS, schema inference, GraphQL introspection), and generates a spec.

This is complementary to our static JS analysis:

| Our static analysis | Vespasian dynamic observation |
|---------------------|-------------------------------|
| Finds endpoints referenced in JS source | Finds endpoints actually called at runtime |
| Misses method + body schema | Captures method, headers, body, response |
| Works offline on any JS file | Requires a live, reachable target |
| Fast | Slower (crawl + probe) |

Running both together maximises coverage: static analysis catches endpoints that dead code paths reference; Vespasian captures the exact wire-level structure of live API calls.

The longer-term goal is to use the Vespasian-generated OpenAPI spec as the foundation for a structured understanding of the target app's API surface — enabling documentation of undocumented functionality and identification of hidden or internal endpoints.

---

## Reference Links

| Resource | URL |
|----------|-----|
| Vespasian repo | https://github.com/praetorian-inc/vespasian |
| Vespasian CLAUDE.md (architecture) | https://raw.githubusercontent.com/praetorian-inc/vespasian/refs/heads/main/CLAUDE.md |
| Vespasian README | https://raw.githubusercontent.com/praetorian-inc/vespasian/refs/heads/main/README.md |
| Katana repo | https://github.com/projectdiscovery/katana |
| OpenAPI 3.0 specification | https://spec.openapis.org/oas/v3.0.3 |
| jsluice repo | https://github.com/hakluke/jsluice |
| Kong CLI library (used by Vespasian) | https://github.com/alecthomas/kong |

---

## Vespasian CLI Reference

Vespasian is invoked as a two-step pipeline.

### Step 1 — Crawl

```bash
vespasian crawl <URL> \
  --depth <int>         \   # default: 3
  --max-pages <int>     \   # default: 100
  --timeout <duration>  \   # default: 10m  (e.g. "5m", "600s")
  --scope same-origin   \   # or same-domain
  -o <capture.json>         # output path; defaults to stdout
```

Produces a `capture.json` file: a JSON array of `ObservedRequest` structs containing method, URL, request headers/body, and response data. This is the canonical intermediate format.

### Step 2 — Generate

```bash
vespasian generate rest <capture.json> \
  --confidence <float>  \   # default: 0.5  (0.0–1.0)
  --probe               \   # default: true  (sends OPTIONS, infers schemas)
  --deduplicate         \   # default: true
  -o <openapi.yaml>         # output path; defaults to stdout
```

`rest` is the positional api-type argument. Also accepts `graphql` and `wsdl`.  
Output for `rest` is always **OpenAPI 3.0 YAML**.

### Alternative: `scan` (combined)

```bash
vespasian scan <URL> \
  --api-type auto     \   # auto | rest | wsdl | graphql
  --depth <int>       \
  --timeout <duration>\
  -o <openapi.yaml>
```

`scan` combines crawl + classify + probe + generate in one command. We use the two-step form for better error isolation (crawl success/failure is logged separately from generation success/failure).

---

## Architecture

### Concurrency Model

```
_discover_with_vespasian(urls, options)
  │
  ├── asyncio.gather(
  │     _discover_with_katana()     ──► JS file URL list ──► ingestion pipeline
  │     _run_vespasian_scan()       ──► openapi.yaml      ──► session artifact
  │   )
  │
  └── return Katana JS file URLs
       (vespasian failure is non-fatal; logged as WARNING)
```

Katana and Vespasian crawl the same target concurrently. Both use Katana internally (Vespasian's crawl subprocess is its own Katana instance), so there are two crawlers hitting the target simultaneously. This is intentional — it is consistent with existing `hybrid` mode (Katana + Playwright), and security reconnaissance tooling is inherently active.

The session is considered complete when both tasks resolve. If Vespasian fails (binary error, crawl timeout, unexpected output), the session completes normally with only Katana results — `hasOpenApiSpec` will be `false` on the session card.

### Storage Layout

```
api/storage/sessions/{session_id}/
  files/                        # existing — ingested JS file content
    {sha256_hash}.js
  openapi.yaml                  # NEW — Vespasian OpenAPI spec (optional)
```

No database schema change is required. `hasOpenApiSpec` is derived at query time from file existence (`Path(f"storage/sessions/{session_id}/openapi.yaml").exists()`).

---

## Data Flow

```
User selects "Vespasian" in Create Session modal
          │
          ▼
POST /api/recon/jobs/start
  discoveryEngine = "vespasian"
          │
          ▼
recon.py validates engine, checks vespasian binary
  → 422 if binary not found
          │
          ▼
ReconJobRunner._discover_target()
  → routes to _discover_with_vespasian()
          │
    ┌─────┴─────┐
    │           │
    ▼           ▼
_discover_with_katana()    _run_vespasian_scan()
JS URLs collected          vespasian crawl → capture.json
                           vespasian generate rest → openapi.yaml
    │           │          copied to storage/sessions/{id}/openapi.yaml
    └─────┬─────┘
          │
          ▼
Katana JS URLs → _prepare_payload_files() → ingestion → analysis
          │
          ▼
Session polling completes
          │
          ▼
GET /api/sessions returns hasOpenApiSpec: true
          │
          ▼
Session card shows OpenAPI download button
          │
          ▼
GET /api/sessions/{id}/openapi
→ streams openapi.yaml as attachment
```

---

## Detailed Change Specification

### 1. `api/app/api/routes/recon.py`

**Current valid engines:** `{"headless", "katana", "hybrid"}`

**Changes:**

1. Add `"vespasian"` to the valid engine set (line ~322):
   ```python
   VALID_ENGINES = {"headless", "katana", "hybrid", "vespasian"}
   ```

2. Add vespasian binary resolution after the katana check (lines ~324–329). Follow the exact same pattern:
   ```python
   vespasian_binary = None
   if engine == "vespasian":
       vespasian_binary = resolve_binary_path("vespasian", env_var="VESPASIAN_BINARY")
       if not vespasian_binary:
           raise HTTPException(
               status_code=422,
               detail=(
                   "Vespasian engine requested but the vespasian binary is not available. "
                   "Install from https://github.com/praetorian-inc/vespasian or set "
                   "VESPASIAN_BINARY env var."
               ),
           )
   ```

3. Pass `vespasian_binary` to `ReconRunnerOptions` construction.

---

### 2. `api/app/services/recon_job_runner.py`

#### 2a. `ReconRunnerOptions` dataclass

Add two new fields:
```python
vespasian_binary: str = "vespasian"
vespasian_timeout_seconds: int = 600  # 10 minutes; independent of katana timeout
```

`vespasian_timeout_seconds` is intentionally separate from `timeout_seconds` (the per-request HTTP timeout used by Katana). Vespasian needs a longer wall-clock budget because it runs a full headless crawl + probe cycle.

#### 2b. `_discover_target()` routing

Add a branch alongside the existing `headless` / `katana` / `hybrid` branches:
```python
elif engine == "vespasian":
    return await self._discover_with_vespasian()
```

#### 2c. `_discover_with_vespasian()` method

```python
async def _discover_with_vespasian(self) -> list[str]:
    """
    Runs Katana (for JS file collection) and Vespasian (for OpenAPI generation)
    concurrently against the same target.

    Returns the JS file URL list from Katana. Vespasian output is a side-channel
    artifact stored in the session directory; its failure is non-fatal.
    """
    katana_task = asyncio.ensure_future(self._discover_with_katana())
    vespasian_task = asyncio.ensure_future(self._run_vespasian_scan())
    results = await asyncio.gather(katana_task, vespasian_task, return_exceptions=True)

    js_urls = results[0] if isinstance(results[0], list) else []
    if isinstance(results[1], Exception):
        logger.warning("Vespasian scan failed (non-fatal): %s", results[1])
    return js_urls
```

#### 2d. `_run_vespasian_scan()` method

```python
async def _run_vespasian_scan(self) -> None:
    """
    Runs `vespasian crawl` then `vespasian generate rest` in a temporary working
    directory, then copies the resulting OpenAPI YAML to the session storage path.

    References:
      Vespasian crawl flags: https://github.com/praetorian-inc/vespasian
      capture.json format: pkg/crawl (ObservedRequest JSON array)
      OpenAPI 3.0 output: pkg/generate/rest
    """
    import tempfile, shutil
    from pathlib import Path

    target_url = self.options.urls[0]
    session_id = self.options.session_id
    binary = self.options.vespasian_binary
    timeout = self.options.vespasian_timeout_seconds
    depth = self.options.max_depth

    storage_dir = Path("storage") / "sessions" / session_id
    storage_dir.mkdir(parents=True, exist_ok=True)
    openapi_dest = storage_dir / "openapi.yaml"

    with tempfile.TemporaryDirectory(prefix=f"vespasian-{session_id[:8]}-") as tmpdir:
        capture_path = Path(tmpdir) / "capture.json"
        spec_path = Path(tmpdir) / "openapi.yaml"

        # Step 1: crawl
        crawl_cmd = [
            binary, "crawl", target_url,
            "--depth", str(depth),
            "--timeout", f"{timeout}s",
            "--scope", "same-origin",
            "-o", str(capture_path),
        ]
        logger.info("Running vespasian crawl: %s", " ".join(crawl_cmd))
        proc = await asyncio.create_subprocess_exec(
            *crawl_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"Vespasian crawl timed out after {timeout}s")

        if proc.returncode != 0 or not capture_path.exists():
            raise RuntimeError(
                f"Vespasian crawl failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace')[:500]}"
            )

        # Step 2: generate OpenAPI
        gen_cmd = [
            binary, "generate", "rest",
            str(capture_path),
            "-o", str(spec_path),
        ]
        logger.info("Running vespasian generate: %s", " ".join(gen_cmd))
        proc = await asyncio.create_subprocess_exec(
            *gen_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("Vespasian generate timed out after 120s")

        if proc.returncode != 0 or not spec_path.exists():
            raise RuntimeError(
                f"Vespasian generate failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace')[:500]}"
            )

        shutil.copy(spec_path, openapi_dest)
        logger.info("OpenAPI spec written to %s", openapi_dest)
```

---

### 3. `api/app/api/routes/sessions.py`

#### 3a. `hasOpenApiSpec` field in session list response

In `list_sessions()`, after building each session dict, add:
```python
from pathlib import Path

session_dict["hasOpenApiSpec"] = (
    Path("storage") / "sessions" / str(session.id) / "openapi.yaml"
).exists()
```

Apply the same addition to any `get_session()` endpoint if it exists.

#### 3b. New download route

```python
@router.get("/{session_id}/openapi")
async def download_session_openapi(session_id: str):
    """
    Stream the Vespasian-generated OpenAPI 3.0 YAML spec for a session.
    Returns 404 if no spec has been generated (engine was not vespasian,
    or vespasian failed).

    OpenAPI 3.0 spec: https://spec.openapis.org/oas/v3.0.3
    """
    from fastapi.responses import FileResponse
    from pathlib import Path

    spec_path = Path("storage") / "sessions" / session_id / "openapi.yaml"
    if not spec_path.exists():
        raise HTTPException(status_code=404, detail="No OpenAPI spec for this session.")

    return FileResponse(
        path=spec_path,
        media_type="application/yaml",
        filename=f"openapi-{session_id[:8]}.yaml",
    )
```

---

### 4. `api/app/templates/dashboard.html`

In the Create Session modal, add the Vespasian option to the discovery engine `<select>` (after "Hybrid"):

```html
<option value="vespasian">Vespasian (Katana + OpenAPI)</option>
```

The full `<select>` will then be:
```html
<select id="create-session-discovery-engine" class="form-select">
    <option value="katana" selected>Katana</option>
    <option value="hybrid">Hybrid (Katana + Headless)</option>
    <option value="headless">Headless only</option>
    <option value="vespasian">Vespasian (Katana + OpenAPI)</option>
</select>
```

---

### 5. `api/app/static/dashboard.js`

#### 5a. Session card button row

In the session card template (around line 3194), add an OpenAPI download button inside the buttons flex container, positioned between "View Summary" and "Delete":

```javascript
${session.hasOpenApiSpec ? `
    <a class="btn btn-outline-secondary btn-sm"
       href="/api/sessions/${session.id}/openapi"
       download="openapi-${session.id.slice(0, 8)}.yaml"
       title="Download OpenAPI 3.0 spec">
        <i class="fas fa-file-code me-1"></i>OpenAPI
    </a>` : ''}
```

#### 5b. Session card badge row

In the badge row (around line 3185), add an "OpenAPI" badge when spec is available:

```javascript
${session.hasOpenApiSpec
    ? '<span class="badge bg-success me-2">OpenAPI</span>'
    : ''}
```

Use `bg-success` (green) to signal a positive artifact is available — consistent with the "Analysis performed" badge.

#### 5c. `collectCreateSessionPayload()` — no change required

The `discoveryEngine` field is already read from the dropdown value as a free string. Adding the new `<option>` in the HTML is sufficient.

---

## Error Handling

| Failure scenario | Behaviour |
|-----------------|-----------|
| Vespasian binary not found at job start | HTTP 422 with install instructions before session is created |
| Vespasian crawl exits non-zero | Non-fatal; WARNING logged; session completes without OpenAPI spec |
| Vespasian crawl timeout | Non-fatal; process killed; WARNING logged |
| Vespasian generate exits non-zero | Non-fatal; WARNING logged |
| Target not reachable by Vespasian | Same as crawl failure — non-fatal |
| `capture.json` empty (target returned no API traffic) | `generate` will produce a minimal or empty spec; stored as-is |
| User downloads spec when none exists | `GET /api/sessions/{id}/openapi` returns 404 |

---

## Session Source Field

When a session is created with `discovery_engine = "vespasian"`, the session's `source` field is set to `"recon_vespasian"` (following the existing pattern in `recon.py` line 346). No additional handling is needed.

---

## Testing Notes

- Unit tests for `_run_vespasian_scan()` should mock `asyncio.create_subprocess_exec` and verify correct command construction, timeout handling, and the copy-to-storage logic.
- Integration test: install vespasian locally (`go install github.com/praetorian-inc/vespasian/cmd/vespasian@latest`) and run against `http://localhost:{port}` using the existing test REST API in `vespasian/test/rest-api/`.
- Frontend: verify the OpenAPI button appears only when `hasOpenApiSpec` is true; verify the download triggers a YAML file save.
- Error path: verify a session with engine=vespasian completes successfully even when vespasian binary fails.

---

## Out of Scope (this iteration)

- Vespasian GraphQL or WSDL generation (REST only for now)
- UI rendering of the OpenAPI spec (download only, not in-browser viewer)
- Feeding the OpenAPI spec back into the JS analysis pipeline
- Encoding normalization pre-pass for the JS extractor (separate initiative)
- JS variable / object key extraction enhancements (separate initiative)
- `vespasianTimeoutSeconds` as a user-configurable form field (uses hardcoded 600s default)
