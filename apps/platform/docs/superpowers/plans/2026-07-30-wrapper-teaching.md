# Wrapper-teaching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an analyst teach the static extractor a custom HTTP-client wrapper (`api.get('/x')`) so its calls become first-class endpoint findings instead of being silently dropped.

**Architecture:** A session-scoped `session_wrapper` config names a wrapper callee; the extractor grows a final dispatch branch that routes `<callee>.<method>(...)` through the existing axios path (tagged with a `wrapper` provenance attribute, `kind` still `"axios"`). A wrapper POST persists the rule and runs an out-of-band, endpoints-only re-extract over the run's stored source blob(s) through the existing idempotent outbox; future runs' analyze stage loads the same config so recognition persists across rescans (REQ-D5).

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, tree-sitter, Alembic, Postgres+RLS, React/Vite/Vitest.

## Global Constraints
- Files cap at ~300 lines; split if exceeded (CLAUDE.md §10). `wrappers.py` stays pure/stdlib-only (no tree-sitter, no DB).
- Tests are colocated: `*_test.py` beside the Python source, `*.test.tsx` beside the component.
- Conventional Commits, multi-line, each ending with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Isolate one logical change per commit.
- Host test lane (no infra): `./.venv/Scripts/python.exe -m pytest -m "not integration"`. Integration tests need live Postgres/Redis/MinIO and are marked at module top with `pytestmark = pytest.mark.integration`.
- FE checks: `cd web && npx vitest run` and `cd web && npx tsc -b --noEmit`.
- No active traffic / no new egress (REQ-P1/P2): re-extract is a static parse over already-stored source only.
- `kind` stays `"axios"` for recovered endpoints (NOT `"wrapper"`) — a `"wrapper"` kind would drop the POST-body Content-Type at `reconstruct.py:176`/`:25`. Provenance lives in a separate `wrapper` attribute (spec §7 / §12 Imp 3).
- RLS for the new table goes through a NEW `WRAPPER_TABLES = ("session_wrapper",)` tuple + migration `0008`, NOT the frozen slice-1 `TENANT_SCOPED_TABLES` (spec §12 Minor 8).
- Dispatch ordering is load-bearing: the wrapper branch is appended LAST in `_dispatch_member`, so a callee colliding with a native target (`axios`, `$`, an `axios.create` instance var, `.open`) resolves via the native/instance path and never double-emits (spec §4 / §12 Minor 7).
- Each task passes the §4 gate-2 higher-model review before merge (CLAUDE.md §4).

---

### Task 1: Extractor wrapper recognition (pure)

Spec §13.1, §4, §7. Add the `WrapperRule` value + callee validator (`wrappers.py`), then wire `extract()` to recognize a taught callee's member calls through the existing axios path, tagging `RawEndpoint.wrapper`. Pure, unit-testable, no DB.

**Files:**
- Create: `src/recon/findings/wrappers.py`
- Create: `src/recon/findings/wrappers_test.py`
- Modify: `src/recon/findings/extract.py`
- Test (existing, extend): `src/recon/findings/extract_test.py`

**Interfaces:**
- Produces `WrapperRule` (frozen): `WrapperRule(callee: str)`.
- Produces `validate_callee(callee: str) -> None` — raises `InvalidWrapperCallee(ValueError)` unless `callee` is a bare JS identifier.
- Produces `wrapper_callees(rules: Sequence[WrapperRule]) -> frozenset[str]`.
- Modifies `extract(source: str | bytes, wrappers: Sequence[WrapperRule] = ()) -> Extraction` (adds `wrappers` param).
- Modifies `RawEndpoint` — adds frozen field `wrapper: str | None = None`.
- Consumes (unchanged) `_axios_member`, `_axios_from_config`, `_endpoint` (thread a `wrapper` kwarg).

#### Steps

- [ ] **1.1 (RED) Write the pure-value + validator tests.** Create `src/recon/findings/wrappers_test.py`:
```python
"""Colocated unit tests for the pure wrapper-rule value + callee validator."""

from __future__ import annotations

import pytest

from recon.findings.wrappers import (
    InvalidWrapperCallee,
    WrapperRule,
    validate_callee,
    wrapper_callees,
)


def test_wrapper_rule_is_frozen_value():
    rule = WrapperRule(callee="api")
    assert rule.callee == "api"
    with pytest.raises(Exception):
        rule.callee = "other"  # type: ignore[misc]  # frozen dataclass


def test_wrapper_callees_builds_a_set():
    assert wrapper_callees([WrapperRule("api"), WrapperRule("apiClient")]) == frozenset(
        {"api", "apiClient"}
    )


@pytest.mark.parametrize("callee", ["api", "apiClient", "_http", "$api", "a1"])
def test_validate_callee_accepts_bare_identifiers(callee):
    validate_callee(callee)  # does not raise


@pytest.mark.parametrize("callee", ["", "1abc", "a.b", "a b", "this.http", "api()"])
def test_validate_callee_rejects_non_identifiers(callee):
    with pytest.raises(InvalidWrapperCallee):
        validate_callee(callee)
```

- [ ] **1.2 (RED-run) Run it, expect FAIL (module missing).**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/wrappers_test.py -q
```
Expected: `ModuleNotFoundError: No module named 'recon.findings.wrappers'` (collection error).

- [ ] **1.3 (GREEN) Create `src/recon/findings/wrappers.py`:**
```python
"""Custom HTTP-client wrapper recognition (REQ-C2 first clause) — pure, stdlib-only.

An analyst teaches the extractor a wrapper by naming its callee (`api`,
`apiClient`); `recon.findings.extract.extract` then treats
`<callee>.<http-method>(path[, body])` and `<callee>.request({url, method})` as
endpoints via the existing axios-member path. This module holds ONLY the value
object, the input validator, and the callee-set helper, so it stays unit-testable
with no tree-sitter or DB dependency (spec §3).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# A bare JS identifier: a member receiver like `api` / `apiClient` / `_http` / `$api`.
# Dotted receivers (`this.http`) and callable wrappers (`api('/x')`) are deferred
# fast-follows (spec §4), so a callee that is not a bare identifier is rejected at
# the door rather than silently never matching.
_CALLEE_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


class InvalidWrapperCallee(ValueError):
    """A wrapper callee that is not a bare JavaScript identifier."""


@dataclass(frozen=True)
class WrapperRule:
    """One taught wrapper: the bare identifier its HTTP calls are made on."""

    callee: str


def validate_callee(callee: str) -> None:
    """Raise :class:`InvalidWrapperCallee` unless `callee` is a bare JS identifier."""
    if not _CALLEE_RE.match(callee or ""):
        raise InvalidWrapperCallee(f"not a bare identifier: {callee!r}")


def wrapper_callees(rules: Sequence[WrapperRule]) -> frozenset[str]:
    """The set of callee identifiers to match in `_dispatch_member` (dispatch-last)."""
    return frozenset(rule.callee for rule in rules)
```

- [ ] **1.4 (GREEN-run) Run it, expect PASS.**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/wrappers_test.py -q
```
Expected: all tests pass.

- [ ] **1.5 (commit)**
```
git add src/recon/findings/wrappers.py src/recon/findings/wrappers_test.py
git commit -m "feat(wrapper-teaching): add pure WrapperRule value + callee validator" -m "New recon.findings.wrappers holds the WrapperRule value object, a bare-JS-identifier callee validator (InvalidWrapperCallee), and the wrapper_callees set helper the extractor dispatch will consume. Pure/stdlib-only so it unit-tests with no tree-sitter or DB (spec Task 13.1, §4)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **1.6 (RED) Add the extractor recognition tests.** Append to `src/recon/findings/extract_test.py`:
```python
# --- taught wrapper recognition (Task 1) -------------------------------------
# A named wrapper callee's member calls are recognized via the existing axios
# path, tagged with `RawEndpoint.wrapper`; `kind` stays "axios". Dispatch order
# is load-bearing: a callee colliding with a native target keeps the native path.

from recon.findings.wrappers import WrapperRule  # noqa: E402


def _wrapped(src: str, callees: list[str]):
    return extract(src, wrappers=[WrapperRule(c) for c in callees]).endpoints


def test_wrapper_member_call_is_recognized():
    eps = _wrapped("const api = makeClient(); api.get('/users');", ["api"])
    assert len(eps) == 1
    assert (eps[0].kind, eps[0].method, eps[0].url, eps[0].wrapper) == (
        "axios", "GET", "/users", "api",
    )


def test_wrapper_request_config_is_recognized():
    # `api.request({url, method})` falls out of the axios reuse for free (spec §4/§12 Minor 6).
    eps = _wrapped("api.request({url:'/x', method:'post'});", ["api"])
    assert (eps[0].method, eps[0].url, eps[0].wrapper) == ("POST", "/x", "api")


def test_wrapper_post_body_params_are_mined():
    eps = _wrapped("api.post('/login', {user:1});", ["api"])
    assert ("user", "body") in {(p.name, p.location) for p in eps[0].params}


def test_untaught_wrapper_still_leaves_no_trace():
    # Regression: without a rule, a wrapper call is dropped exactly as today.
    result = extract("api.get('/users');")
    assert result.endpoints == [] and result.unattributed == 0


def test_native_axios_collision_takes_native_path_not_wrapper():
    # `axios` taught as a callee must still resolve via the native branch
    # (dispatch-last), so NO wrapper tag is attached (spec §4/§12 Minor 7).
    eps = _wrapped("axios.get('/x');", ["axios"])
    assert (eps[0].kind, eps[0].url, eps[0].wrapper) == ("axios", "/x", None)


def test_axios_create_instance_collision_keeps_base_not_wrapper():
    # An axios.create instance var named like the wrapper keeps its real base
    # (instance branch precedes the wrapper branch); tag stays None, base applies.
    eps = _wrapped(
        "const api = axios.create({baseURL:'/b'}); api.get('/x');", ["api"]
    )
    assert ("GET", "/b/x", None) in [(e.method, e.url, e.wrapper) for e in eps]


def test_wrapper_dynamic_arg_is_unattributed_like_axios():
    # A non-static URL leaves the same honest trace axios would (REQ-C2).
    result = extract("api.get(dynamicUrl);", wrappers=[WrapperRule("api")])
    assert result.endpoints == [] and result.unattributed == 1
```

- [ ] **1.7 (RED-run) Run it, expect FAIL.**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/extract_test.py -q -k wrapper
```
Expected: `TypeError: extract() got an unexpected keyword argument 'wrappers'` (and `AttributeError: 'RawEndpoint' object has no attribute 'wrapper'`).

- [ ] **1.8 (GREEN) Add the `wrapper` field to `RawEndpoint`.** In `src/recon/findings/extract.py`, replace the `RawEndpoint` class (currently ends with `snippet: str`):
```python
@dataclass(frozen=True)
class RawEndpoint:
    kind: str  # fetch | xhr | axios | jquery | websocket
    method: str
    url: str
    params: tuple[RawParam, ...]
    line: int
    col: int
    start_byte: int
    end_byte: int
    snippet: str
    # Provenance: the callee of the taught wrapper this endpoint came from, else
    # None. NOT folded into `kind` — `kind` stays "axios" so the POST-body
    # Content-Type gate at reconstruct.py:176 still fires (spec §7 / §12 Imp 3).
    wrapper: str | None = None
```

- [ ] **1.9 (GREEN) Import the wrapper helpers + `Sequence`.** In `src/recon/findings/extract.py`, add to the import block (after `from urllib.parse import parse_qsl`):
```python
from collections.abc import Sequence
```
and after the `import tree_sitter_javascript as tsjs` line, add:
```python
from recon.findings.wrappers import WrapperRule, wrapper_callees
```

- [ ] **1.10 (GREEN) Thread `wrappers` through `extract()`.** Replace the `extract` function body:
```python
def extract(source: str | bytes, wrappers: Sequence[WrapperRule] = ()) -> Extraction:
    """Extract network endpoints from JavaScript source.

    `wrappers` names custom HTTP-client callees (`api`, `apiClient`) whose member
    calls are recognized via the axios path (spec §4); empty = today's fixed set only.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = _PARSER.parse(data)
    env = collect_base_env(tree.root_node, data)
    callees = wrapper_callees(wrappers)
    result = Extraction()
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            _handle_call(node, result, env, callees)
        elif node.type == "new_expression":
            _handle_new(node, result)
    return result
```

- [ ] **1.11 (GREEN) Thread `callees` into `_handle_call`.** Replace the `_handle_call` signature line and its final dispatch line:
```python
def _handle_call(call: Node, result: Extraction, env: BaseEnv, callees: frozenset[str]) -> None:
```
and at the end of `_handle_call`, replace `_dispatch_member(call, obj, prop, result, env)` with:
```python
    _dispatch_member(call, obj, prop, result, env, callees)
```

- [ ] **1.12 (GREEN) Append the final wrapper branch to `_dispatch_member`.** Replace the whole `_dispatch_member` function:
```python
def _dispatch_member(
    call: Node, obj: str, prop: str, result: Extraction, env: BaseEnv, callees: frozenset[str]
) -> None:
    if prop == "fetch" and obj in _GLOBAL_OBJECTS:
        _fetch(call, result, env)
    elif prop == "open":  # ANY receiver's `.open(method, url)` is XHR, checked before instances
        _xhr_open(call, result)
    elif obj == "axios":
        _axios_member(call, prop, result, env, base=env.default_base or "")
    elif obj in _JQUERY:
        _jquery(call, prop, result)
    elif obj in env.instances:
        base = env.instances[obj]  # may be None (recognized instance, unknown base)
        _axios_member(call, prop, result, env, base=base or "")
    elif obj in callees:  # taught wrapper — MUST be last so native/instance collisions win
        _axios_member(call, prop, result, env, base="", wrapper=obj)
```

- [ ] **1.13 (GREEN) Thread `wrapper` into `_axios_member`.** Replace the whole `_axios_member` function:
```python
def _axios_member(
    call: Node, prop: str, result: Extraction, env: BaseEnv, base: str = "",
    wrapper: str | None = None,
) -> None:
    args = _args(call)
    if prop == "request" and args and args[0].type == "object":
        _axios_from_config(args[0], call, result, env, base=base, wrapper=wrapper)
        return
    if prop.upper() not in HTTP_METHODS:
        return
    url = _resolve_url(args[0], env, base) if args else None
    if url is None:
        result.unattributed += 1
        return
    params = _query_params(url)
    if prop.upper() in ("POST", "PUT", "PATCH"):
        # axios.post(url, data[, config])
        if len(args) >= 2:
            params += _body_params_from_value(args[1])
        if len(args) >= 3:
            params += _config_query_params(args[2])
    elif len(args) >= 2:
        # axios.get/delete/head(url[, config]) — query params live in the config
        params += _config_query_params(args[1])
    result.endpoints.append(_endpoint("axios", prop, url, params, call, wrapper=wrapper))
```

- [ ] **1.14 (GREEN) Thread `wrapper` into `_axios_from_config`.** Replace its signature and its `_endpoint(...)` append line:
```python
def _axios_from_config(
    config: Node, call: Node, result: Extraction, env: BaseEnv, base: str = "",
    wrapper: str | None = None,
) -> None:
```
and replace the final line `result.endpoints.append(_endpoint("axios", method, url, params, call))` with:
```python
    result.endpoints.append(_endpoint("axios", method, url, params, call, wrapper=wrapper))
```

- [ ] **1.15 (GREEN) Thread `wrapper` into `_endpoint`.** Replace the whole `_endpoint` function:
```python
def _endpoint(
    kind: str, method: str, url: str, params: list[RawParam], call: Node,
    wrapper: str | None = None,
) -> RawEndpoint:
    row, col = call.start_point
    deduped = list(dict.fromkeys(params))  # preserve order, drop repeats
    return RawEndpoint(
        kind=kind,
        method=method.upper(),
        url=url,
        params=tuple(deduped),
        line=row + 1,
        col=col,
        start_byte=call.start_byte,
        end_byte=call.end_byte,
        snippet=_text(call)[:200],
        wrapper=wrapper,
    )
```

- [ ] **1.16 (GREEN-run) Run the new + full extractor suite, expect PASS.**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/extract_test.py src/recon/findings/wrappers_test.py -q
```
Expected: all pass, including the pre-existing extractor + base-env cases (the `wrapper` field defaults to `None` on every native path, so no regression).

- [ ] **1.17 (commit)**
```
git add src/recon/findings/extract.py src/recon/findings/extract_test.py
git commit -m "feat(wrapper-teaching): recognize taught wrapper calls in extract()" -m "extract() gains a wrappers param; RawEndpoint gains an optional frozen wrapper provenance field. A final _dispatch_member branch (after axios/jQuery/xhr-open/env.instances) routes a taught callee through _axios_member(base=''), so api.get('/x') and api.request({url,method}) become endpoints with kind='axios' and wrapper set. Dispatch-last keeps native/instance collisions on the native path (spec Task 13.1, §4, §7)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Endpoints-only re-extract

Spec §13.2, §3, §6, §12 (Blockers 1-2, Imp 4, Minor 9). Factor an endpoints-only helper out of `_analyze_blob` (no Kingfisher subprocess, no `analyze.coverage` event), surface the `wrapper` provenance in `_record_endpoint`, and add the out-of-band `reextract.py` service.

**Files:**
- Modify: `src/recon/findings/analyze.py`
- Create: `src/recon/findings/reextract.py`
- Create: `src/recon/findings/reextract_test.py` (integration)

**Interfaces:**
- Produces `_extract_endpoints(session, *, tenant_id: str, run_id: str, source: str, source_map_ref: str | None, run_asset_id: str | None, asset_url: str | None, wrappers: Sequence[WrapperRule] = ()) -> _EndpointExtraction`.
- Produces `_EndpointExtraction(written: int, attributed: int, unattributed: int, sources_recovered: int, source_map: str, files: tuple[FileCoverage, ...])`.
- Modifies `_analyze_blob(...)` — add trailing `wrappers: Sequence[WrapperRule] = ()`; now delegates the endpoint loop to `_extract_endpoints`.
- Modifies `_record_endpoint(...)` — adds `attributes["wrapper"]` when `ep.wrapper` is set.
- Produces `reextract_run(tenant_id: str, run_id: str, wrappers: Sequence[WrapperRule]) -> int | None` (rows written, or None for RLS-invisible/unknown run).
- Produces `SourceBlobMissing(Exception)`.
- Consumes `storage.get_blob` (`storage.py:70`), `run_assets.list_for_run`/`AssetRow` (`runs/assets.py:24,45`), `models.Run`, `store.record_finding` (via `_record_endpoint`).

#### Steps

- [ ] **2.1 (REGRESSION baseline) Confirm the analyze suite is green before refactoring.**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_test.py src/recon/findings/analyze_multi_test.py src/recon/findings/analyze_secret_redaction_test.py -q -m integration
```
Expected: PASS (needs live PG/Redis/MinIO). This is the behavior the refactor must preserve exactly.

- [ ] **2.2 (GREEN, refactor) Add imports to `analyze.py`.** In `src/recon/findings/analyze.py`, add after `from __future__ import annotations`:
```python
from collections.abc import Sequence
```
and add to the `from recon.findings...` group (alongside `from recon.findings.extract import RawEndpoint, extract`):
```python
from recon.findings.wrappers import WrapperRule
```

- [ ] **2.3 (GREEN, refactor) Add the `_EndpointExtraction` result + `_extract_endpoints` helper.** In `src/recon/findings/analyze.py`, insert immediately BEFORE `def _analyze_blob(`:
```python
@dataclass(frozen=True)
class _EndpointExtraction:
    """Endpoint-loop result for one blob (no secrets, no coverage event). The
    coverage counters ride along so `_analyze_blob` can still build its
    `analyze.coverage` payload; the out-of-band re-extract ignores them."""

    written: int
    attributed: int
    unattributed: int
    sources_recovered: int
    source_map: str
    files: tuple[FileCoverage, ...]


def _extract_endpoints(
    session,
    *,
    tenant_id: str,
    run_id: str,
    source: str,
    source_map_ref: str | None,
    run_asset_id: str | None,
    asset_url: str | None,
    wrappers: Sequence[WrapperRule] = (),
) -> _EndpointExtraction:
    """Extract + record ONLY endpoint/param findings for one blob.

    Shared core of the analyze stage (`_analyze_blob`, which additionally scans
    secrets + emits coverage) and the out-of-band wrapper re-extract
    (`recon.findings.reextract`, which calls this directly so a wrapper POST
    records findings WITHOUT re-emitting the run's coverage counters — spec
    §2.6/§12 Blocker 1 — and WITHOUT the Kingfisher subprocess — §12 Blocker 2).
    Retains `_analysis_units(source_map_ref, source)` so a re-emitted native
    endpoint keeps its source-map-recovered path and thus its stable
    `finding_hash` (§12 Imp 4)."""
    units, source_map_status, sources_recovered = _analysis_units(source_map_ref, source)
    attributed = 0
    unattributed = 0
    written = 0
    per_file: dict[str, list[int]] = {}
    for source_name, unit_text in units:
        extraction = extract(unit_text, wrappers=wrappers)
        path = normalize.normalize_source_path(source_name)
        attributed += len(extraction.endpoints)
        unattributed += extraction.unattributed
        bucket = per_file.setdefault(path, [0, 0])
        bucket[0] += len(extraction.endpoints)
        bucket[1] += extraction.unattributed
        for endpoint in extraction.endpoints:
            written += _record_endpoint(
                session, tenant_id, run_id, path, source_name, endpoint,
                run_asset_id=run_asset_id, asset_url=asset_url,
            )
    files = tuple(
        FileCoverage(path=path, attributed=counts[0], unattributed=counts[1])
        for path, counts in sorted(per_file.items())
    )
    return _EndpointExtraction(
        written=written, attributed=attributed, unattributed=unattributed,
        sources_recovered=sources_recovered, source_map=source_map_status, files=files,
    )
```

- [ ] **2.4 (GREEN, refactor) Rewrite `_analyze_blob` to delegate the endpoint loop.** In `src/recon/findings/analyze.py`, replace the whole body of `_analyze_blob` (keep its docstring) from `raw = storage.get_blob(input_ref)` through `return coverage, coverage_event`, and add the `wrappers` param to the signature:
```python
def _analyze_blob(
    session,
    *,
    tenant_id: str,
    run_id: str,
    input_ref: str,
    source_map_ref: str | None,
    run_asset_id: str | None,
    asset_url: str | None,
    wrappers: Sequence[WrapperRule] = (),
) -> tuple[Coverage, RecordedEvent]:
    raw = storage.get_blob(input_ref)
    source = raw.decode("utf-8", "replace")
    # Secret scanning runs out-of-process. A missing binary degrades coverage
    # (status recorded on the event); a genuine engine failure raises here and
    # fails/retries the stage rather than under-reporting secrets.
    scan = kingfisher.scan(raw)

    endpoints = _extract_endpoints(
        session, tenant_id=tenant_id, run_id=run_id, source=source,
        source_map_ref=source_map_ref, run_asset_id=run_asset_id, asset_url=asset_url,
        wrappers=wrappers,
    )
    written = endpoints.written

    # Secrets are scanned on the original bundle this slice (input.js path).
    secret_path = normalize.normalize_source_path(_SOURCE_NAME)
    # Per (rule, snippet) search cursor so N identical secret sightings map to N
    # distinct byte offsets (distinct occurrences, REQ-C2) instead of collapsing.
    secret_cursors: dict[tuple[str, str], int] = {}
    for secret in scan.secrets:
        written += _record_secret(
            session, tenant_id, run_id, secret_path, source, secret, secret_cursors,
            run_asset_id=run_asset_id, asset_url=asset_url,
        )
    coverage_event = record_event(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type="analyze.coverage",
        payload={
            "attributed": endpoints.attributed,
            "unattributed": endpoints.unattributed,
            "secrets": len(scan.secrets),
            "secrets_engine": scan.status,
            "sources_recovered": endpoints.sources_recovered,
            "source_map": endpoints.source_map,
            "files": [
                {"path": f.path, "attributed": f.attributed, "unattributed": f.unattributed}
                for f in endpoints.files
            ],
        },
    )
    coverage = Coverage(
        endpoints.attributed, endpoints.unattributed, written,
        secrets=len(scan.secrets), secrets_engine=scan.status,
        sources_recovered=endpoints.sources_recovered, source_map=endpoints.source_map,
        files=endpoints.files,
    )
    return coverage, coverage_event
```

- [ ] **2.5 (GREEN, refactor) Surface the wrapper provenance in `_record_endpoint`.** In `src/recon/findings/analyze.py`, inside `_record_endpoint`, replace the endpoint `_write(...)` call's `attributes=` argument. Change:
```python
        attributes={"kind": ep.kind, "method": ep.method},
```
to build the dict first so the tag is present only when set (spec §7):
```python
        attributes=(
            {"kind": ep.kind, "method": ep.method, "wrapper": ep.wrapper}
            if ep.wrapper
            else {"kind": ep.kind, "method": ep.method}
        ),
```

- [ ] **2.6 (GREEN-run, refactor) Re-run the analyze suite, expect PASS (behavior preserved).**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_test.py src/recon/findings/analyze_multi_test.py src/recon/findings/analyze_secret_redaction_test.py -q -m integration
```
Expected: PASS — identical coverage events + findings; the `wrapper` attribute is absent on every native endpoint (`ep.wrapper is None`).

- [ ] **2.7 (commit)**
```
git add src/recon/findings/analyze.py
git commit -m "refactor(wrapper-teaching): factor endpoints-only _extract_endpoints out of _analyze_blob" -m "The _analysis_units -> extract -> _record_endpoint loop becomes _extract_endpoints (endpoints only: no kingfisher.scan, no analyze.coverage event). _analyze_blob now wraps it with secrets + coverage; _record_endpoint surfaces RawEndpoint.wrapper as attributes['wrapper']. Behavior for the analyze stage is unchanged (spec Task 13.2, §3, §6, §7, §12 Blocker 1/2, Imp 4)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **2.8 (RED) Write the re-extract integration tests.** Create `src/recon/findings/reextract_test.py`:
```python
"""Integration tests for the out-of-band wrapper re-extract (spec §6).

Requires the full compose stack (Postgres, Redis, MinIO): a re-extract re-reads a
run's stored source blob(s) and records wrapper endpoints through the idempotent
outbox, without re-emitting coverage or transitioning run state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import AssetStatus
from recon.findings import analyze, queries, reextract
from recon.findings.normalize import finding_hash
from recon.findings.wrappers import WrapperRule
from recon.runs import service

pytestmark = pytest.mark.integration


def _seed_single(redis, tenant, session_id, source: bytes) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", source)
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run).where(models.Run.id == view.id).values(input_ref=key)
        )
    return view.id


def _seed_crawl(redis, tenant, session_id, blobs: dict[str, bytes]) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    with tenant_session(tenant) as session:
        for url, src in blobs.items():
            key = storage.put_blob(tenant, view.id, "input", src)
            session.add(models.RunAsset(
                tenant_id=tenant, run_id=view.id, url=url, input_ref=key,
                fetch_status=AssetStatus.OK.value, analyze_status=AssetStatus.PENDING.value,
            ))
    return view.id


def _endpoint_findings(tenant, run_id) -> dict[str, models.Finding]:
    with tenant_session(tenant) as session:
        rows = session.execute(
            select(models.Finding).where(
                models.Finding.run_id == run_id, models.Finding.type == "endpoint",
            )
        ).scalars().all()
        return {r.value: r for r in rows}


def _coverage_event_count(tenant, run_id) -> int:
    with tenant_session(tenant) as session:
        return len(session.execute(
            select(models.RunEvent.id).where(
                models.RunEvent.run_id == run_id,
                models.RunEvent.type == "analyze.coverage",
            )
        ).all())


def test_reextract_recovers_wrapper_endpoint(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_single(redis, tenant, session_id, b"const api = makeClient(); api.get('/users');")

    written = reextract.reextract_run(tenant, run_id, [WrapperRule("api")])

    assert written >= 1
    found = _endpoint_findings(tenant, run_id)
    assert "GET /users" in found
    assert found["GET /users"].attributes["wrapper"] == "api"
    assert found["GET /users"].attributes["kind"] == "axios"
    # Reaches the downstream read path classify/probe/export consume.
    listed = queries.list_findings(tenant, run_id)
    assert any(f.value == "GET /users" for f in listed.findings)


def test_reextract_preserves_native_hashes_and_adds_wrapper(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_single(
        redis, tenant, session_id,
        b"fetch('/native'); const api = makeClient(); api.get('/w');",
    )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)  # natives recorded
    before = set(_endpoint_findings(tenant, run_id))
    assert "GET /native" in before and "GET /w" not in before

    reextract.reextract_run(tenant, run_id, [WrapperRule("api")])

    after = set(_endpoint_findings(tenant, run_id))
    assert before <= after  # native endpoints not churned (§12 Imp 4)
    assert after - before == {"GET /w"}
    # The native finding_hash is exactly the pre-wrapper identity (path input.js).
    native = _endpoint_findings(tenant, run_id)["GET /native"]
    assert native.finding_hash == finding_hash("endpoint", "GET /native", "input.js")


def test_reextract_is_idempotent(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_single(redis, tenant, session_id, b"const api = makeClient(); api.get('/u');")

    first = reextract.reextract_run(tenant, run_id, [WrapperRule("api")])
    values1 = set(_endpoint_findings(tenant, run_id))
    second = reextract.reextract_run(tenant, run_id, [WrapperRule("api")])
    values2 = set(_endpoint_findings(tenant, run_id))

    assert first >= 1 and second == 0  # re-run writes nothing new (outbox no-op)
    assert values1 == values2


def test_reextract_multi_asset_does_not_reemit_coverage(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed_crawl(
        redis, tenant, session_id,
        {"https://acme.io/a.js": b"const api = makeClient(); api.get('/a');"},
    )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)  # one asset -> one coverage event
    before = _coverage_event_count(tenant, run_id)

    reextract.reextract_run(tenant, run_id, [WrapperRule("api")])

    assert _coverage_event_count(tenant, run_id) == before  # no coverage double-count (§12 Blocker 1)
    assert "GET /a" in _endpoint_findings(tenant, run_id)


def test_reextract_unknown_run_is_none(redis, authorized_session):
    tenant, _session_id = authorized_session
    assert reextract.reextract_run(
        tenant, "00000000-0000-0000-0000-000000000000", [WrapperRule("api")]
    ) is None


def test_reextract_missing_blob_raises(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run).where(models.Run.id == view.id)
            .values(input_ref=f"{tenant}/{view.id}/input/deadbeef")  # no such object
        )
    with pytest.raises(reextract.SourceBlobMissing):
        reextract.reextract_run(tenant, view.id, [WrapperRule("api")])
```

- [ ] **2.9 (RED-run) Run it, expect FAIL (module missing).**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/reextract_test.py -q -m integration
```
Expected: `ModuleNotFoundError: No module named 'recon.findings.reextract'` (collection error).

- [ ] **2.10 (GREEN) Create `src/recon/findings/reextract.py`:**
```python
"""Out-of-band wrapper re-extract (REQ-C2 first clause) — spec §6.

Re-reads a terminal run's stored source blob(s) and records the endpoint findings
recognized under a set of taught wrapper rules, through the existing idempotent
outbox (REQ-A3). Records ONLY endpoints — no Kingfisher subprocess, no
`analyze.coverage` event (spec §2.6/§12 Blocker 1) — and never transitions run
state, mirroring `recon.spec.service.reclassify_run`. Each blob is read in its own
`tenant_session`, so a run invisible to the tenant (RLS) resolves to `None` (the
router maps that to 404). A vanished source blob maps to a clean
`SourceBlobMissing` (§12 Minor 9) rather than a raw 500.

`_extract_endpoints` is imported deliberately: the spec (§3/§6) names it as the
endpoints-only core re-extract calls directly, bypassing `_analyze_blob`'s
secrets + coverage.
"""

from __future__ import annotations

from collections.abc import Sequence

from botocore.exceptions import ClientError

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import AssetStatus
from recon.findings.analyze import _extract_endpoints
from recon.findings.wrappers import WrapperRule
from recon.runs import assets as run_assets


class SourceBlobMissing(Exception):
    """A run's stored source blob is gone — re-extract cannot proceed (spec §12 Minor 9)."""


def reextract_run(tenant_id: str, run_id: str, wrappers: Sequence[WrapperRule]) -> int | None:
    """Re-extract `run_id` under `wrappers`; return the number of finding/occurrence
    rows newly written (0 when nothing is new — the outbox is idempotent), or `None`
    if the run is invisible to `tenant_id` (RLS) or does not exist.

    Run-scoped by design (spec §2.5/§12 Minor 5): re-reads only THIS run's blobs,
    not every sibling run in the session."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        input_ref = run.input_ref
        source_map_ref = run.source_map_ref

    rows = run_assets.list_for_run(tenant_id, run_id)
    written = 0
    try:
        if rows:  # multi-asset (crawl) run: one blob per fetched asset, no source map
            for asset in rows:
                if asset.fetch_status != AssetStatus.OK.value or not asset.input_ref:
                    continue
                with tenant_session(tenant_id) as session:
                    written += _reextract_blob(
                        session, tenant_id=tenant_id, run_id=run_id,
                        input_ref=asset.input_ref, source_map_ref=None,
                        run_asset_id=asset.id, asset_url=asset.url, wrappers=wrappers,
                    )
        elif input_ref:  # legacy single-blob run (with its own source map, if any)
            with tenant_session(tenant_id) as session:
                written += _reextract_blob(
                    session, tenant_id=tenant_id, run_id=run_id,
                    input_ref=input_ref, source_map_ref=source_map_ref,
                    run_asset_id=None, asset_url=None, wrappers=wrappers,
                )
    except ClientError as exc:  # storage.get_blob on a vanished blob (§12 Minor 9)
        raise SourceBlobMissing(str(exc)) from exc
    return written


def _reextract_blob(
    session, *, tenant_id: str, run_id: str, input_ref: str,
    source_map_ref: str | None, run_asset_id: str | None, asset_url: str | None,
    wrappers: Sequence[WrapperRule],
) -> int:
    raw = storage.get_blob(input_ref)
    source = raw.decode("utf-8", "replace")
    return _extract_endpoints(
        session, tenant_id=tenant_id, run_id=run_id, source=source,
        source_map_ref=source_map_ref, run_asset_id=run_asset_id, asset_url=asset_url,
        wrappers=wrappers,
    ).written
```

- [ ] **2.11 (GREEN-run) Run it, expect PASS.**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/reextract_test.py -q -m integration
```
Expected: all six tests pass.

- [ ] **2.12 (commit)**
```
git add src/recon/findings/reextract.py src/recon/findings/reextract_test.py
git commit -m "feat(wrapper-teaching): add out-of-band endpoints-only re-extract service" -m "reextract_run re-reads a run's stored source blob(s) (single run.input_ref or each ok run_asset.input_ref) and records wrapper endpoints via _extract_endpoints -> the idempotent outbox. No coverage re-emit, no state transition; own tenant_session per blob (RLS-invisible -> None -> router 404); a missing blob maps to SourceBlobMissing (spec Task 13.2, §6, §12 Minor 5/9)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Model + migration + service + router (+ future-run analyze wiring)

Spec §13.3, §5, §8, §11 (REQ-D5/REQ-S1). Persist the session-scoped config, expose the write path, and wire the analyze stage so future runs recognize a taught wrapper automatically.

> **Note on REQ-D5 future-run wiring (steps 3.16-3.20):** the spec's §3 diagram + §11 REQ-D5 row require the analyze stage of *future* runs to load the session's wrapper config, but the spec's enumerated unit list does not itemize this edit. It is folded here (its `SessionWrapper` model + `queries` loader exist only from this task on).

**Files:**
- Modify: `src/recon/db/models.py`
- Create: `src/recon/migrations/versions/0008_session_wrapper.py`
- Create: `src/recon/findings/wrapper_service.py`
- Create: `src/recon/findings/wrapper_service_test.py` (integration)
- Modify: `src/recon/findings/queries.py`
- Create: `src/recon/api/wrappers_router.py`
- Create: `src/recon/api/wrappers_router_test.py` (integration)
- Modify: `src/recon/api/app.py`
- Modify: `src/recon/findings/analyze.py`
- Create: `src/recon/findings/analyze_wrapper_test.py` (integration)

**Interfaces:**
- Produces `models.SessionWrapper` (cols `id, tenant_id, session_id, callee, actor, created_at, updated_at`; `UNIQUE(session_id, callee)`) + `models.WRAPPER_TABLES = ("session_wrapper",)`.
- Produces migration `0008_session_wrapper` (`down_revision = "0007_session_base_url"`).
- Produces `queries.wrapper_rules_in_session(session, session_id: str) -> list[WrapperRule]`.
- Produces `wrapper_service.add_rule(tenant_id: str, run_id: str, *, callee: str, actor: str | None = None) -> dict | None` returning `{"rule": {"id","callee","actor"}, "recovered": int}`; `list_rules(...) -> list[dict] | None`; `delete_rule(...) -> bool | None`.
- Produces routes `POST/GET/DELETE /runs/{run_id}/wrappers` (POST body `WrapperRuleIn(callee: str, actor: str | None = None)`).
- Consumes `reextract.reextract_run` / `reextract.SourceBlobMissing`, `wrappers.validate_callee` / `InvalidWrapperCallee`, `api.deps.get_tenant_id`.
- Modifies `analyze.analyze_run` / `_analyze_assets` — load + thread `wrappers`.

#### Steps

- [ ] **3.1 (GREEN, no test yet) Add the `SessionWrapper` model + `WRAPPER_TABLES`.** In `src/recon/db/models.py`, insert after the `SessionBaseUrl` class (before `# Tables carrying a tenant_id get FORCE RLS in the migration.`):
```python
class SessionWrapper(Base):
    """A taught HTTP-client wrapper for a session (REQ-C2 first clause).

    Session-scoped (survives continuous rescans, REQ-D5) like ``session_spec`` /
    ``session_base_url``: the analyze stage and the out-of-band re-extract both read
    it to recognize ``<callee>.<method>(...)`` calls. ``UNIQUE(session_id, callee)``
    — one rule per callee; the POST upserts on it."""

    __tablename__ = "session_wrapper"
    __table_args__ = (
        UniqueConstraint("session_id", "callee", name="uq_session_wrapper_session_callee"),
        Index("ix_session_wrapper_session", "tenant_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    callee: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_col(nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col(nullable=False)
```
and at the very end of the file, after the `BASE_URL_TABLES` line:
```python
# REQ-C2 wrapper-teaching addition, RLS-enabled by migration 0008.
WRAPPER_TABLES: tuple[str, ...] = ("session_wrapper",)
```

- [ ] **3.2 (GREEN) Create the migration `src/recon/migrations/versions/0008_session_wrapper.py`:**
```python
"""REQ-C2 wrapper-teaching rules (session_wrapper) + RLS

Revision ID: 0008_session_wrapper
Revises: 0007_session_base_url
Create Date: 2026-07-30

Mirrors 0007: a brand-new table built from live model metadata (create_all is
idempotent — only what's missing), then FORCE row-level security + the
tenant_isolation policy + an explicit GRANT (REQ-S1).
"""

from __future__ import annotations

from alembic import op

from recon.db import models
from recon.db.base import Base

revision = "0008_session_wrapper"
down_revision = "0007_session_base_url"
branch_labels = None
depends_on = None

APP_ROLE = "recon_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # idempotent: builds only the new table

    for table in models.WRAPPER_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            "USING (tenant_id::text = current_setting('app.current_tenant', true)) "
            "WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))"
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO {APP_ROLE}')


def downgrade() -> None:
    for table in models.WRAPPER_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.drop_table("session_wrapper")
```

- [ ] **3.3 (GREEN-run) Confirm the migration applies to head.**
```
./.venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head
```
Expected: no error; `session_wrapper` created with RLS. (Migrations also auto-apply once per integration session via `conftest.py::migrated`.)

- [ ] **3.4 (commit)**
```
git add src/recon/db/models.py src/recon/migrations/versions/0008_session_wrapper.py
git commit -m "feat(wrapper-teaching): add session_wrapper model + migration 0008 (RLS)" -m "Session-scoped SessionWrapper (UNIQUE(session_id, callee)) + a new WRAPPER_TABLES tuple, NOT the frozen slice-1 TENANT_SCOPED_TABLES. Migration 0008 mirrors 0007: create_all + FORCE RLS + tenant_isolation USING/WITH CHECK + GRANT (spec Task 13.3, §5, §11 REQ-S1/§12 Minor 8)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **3.5 (GREEN, loader) Add `wrapper_rules_in_session` to `queries.py`.** In `src/recon/findings/queries.py`, add `SessionWrapper` to the `from recon.db.models import (...)` list, add `from recon.findings.wrappers import WrapperRule` after the existing `from recon.findings.base_url import BaseUrlRule`, and insert this function immediately after `base_url_rules_in_session`:
```python
def wrapper_rules_in_session(session, session_id: str) -> list[WrapperRule]:
    """Every taught wrapper callee for a session, as pure WrapperRule values. Takes
    an OPEN tenant session so a caller can load rules inside its own transaction
    (mirrors ``base_url_rules_in_session``)."""
    rows = session.scalars(
        select(SessionWrapper)
        .where(SessionWrapper.session_id == session_id)
        .order_by(SessionWrapper.created_at)
    ).all()
    return [WrapperRule(callee=row.callee) for row in rows]
```

- [ ] **3.6 (RED) Write the service integration tests.** Create `src/recon/findings/wrapper_service_test.py`:
```python
import pytest
from sqlalchemy import select

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import wrapper_service
from recon.findings.wrappers import InvalidWrapperCallee
from recon.runs import service

pytestmark = pytest.mark.integration


def _run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def _run_with_source(redis, tenant, session_id, source: bytes) -> str:
    from sqlalchemy import update
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", source)
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))
    return view.id


def test_add_list_delete_wrapper_rule(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)

    res = wrapper_service.add_rule(tenant, run_id, callee="api")
    assert res["rule"]["callee"] == "api"

    rules = wrapper_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["callee"] == "api"

    assert wrapper_service.delete_rule(tenant, run_id, res["rule"]["id"]) is True
    assert wrapper_service.list_rules(tenant, run_id) == []


def test_add_wrapper_upserts_on_callee(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    wrapper_service.add_rule(tenant, run_id, callee="api", actor="a")
    wrapper_service.add_rule(tenant, run_id, callee="api", actor="b")
    rules = wrapper_service.list_rules(tenant, run_id)
    assert len(rules) == 1 and rules[0]["actor"] == "b"  # second upsert overwrote actor


def test_add_wrapper_invalid_callee_raises(authorized_session):
    tenant, session_id = authorized_session
    run_id = _run(tenant, session_id)
    with pytest.raises(InvalidWrapperCallee):
        wrapper_service.add_rule(tenant, run_id, callee="a.b")


def test_add_wrapper_unknown_run_is_none(authorized_session):
    tenant, _session_id = authorized_session
    assert wrapper_service.add_rule(
        tenant, "00000000-0000-0000-0000-000000000000", callee="api"
    ) is None


def test_add_wrapper_reextracts_and_recovers_endpoint(redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_source(redis, tenant, session_id, b"const api = makeClient(); api.get('/svc');")

    res = wrapper_service.add_rule(tenant, run_id, callee="api")

    assert res["recovered"] >= 1
    with tenant_session(tenant) as session:
        values = {
            f.value for f in session.execute(
                select(models.Finding).where(
                    models.Finding.run_id == run_id, models.Finding.type == "endpoint",
                )
            ).scalars()
        }
    assert "GET /svc" in values
```

- [ ] **3.7 (RED-run) Run it, expect FAIL (module missing).**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/wrapper_service_test.py -q -m integration
```
Expected: `ModuleNotFoundError: No module named 'recon.findings.wrapper_service'`.

- [ ] **3.8 (GREEN) Create `src/recon/findings/wrapper_service.py`:**
```python
"""REQ-C2 wrapper-teaching — the write path (spec §6).

Validate + persist a taught wrapper callee into the run's session, then re-extract
the run's stored source so its wrapper calls surface as findings. `None` when the
run is invisible to the tenant (RLS) -> the router maps that to 404.

Two-transaction note (mirrors base_url_service): the rule is persisted in one
tenant_session, then reextract_run opens its own. Harmless — the outbox is
idempotent and the persisted rule is committed before the re-extract reads.
`DELETE` only removes the rule and does NOT re-extract: per spec §8 the outbox
cannot retract already-persisted wrapper findings, so a re-extract on delete would
be a pure no-op.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from recon.db import models
from recon.db.base import tenant_session
from recon.findings import queries, reextract
from recon.findings.wrappers import validate_callee


def _as_dict(row: models.SessionWrapper) -> dict:
    return {"id": str(row.id), "callee": row.callee, "actor": row.actor}


def add_rule(tenant_id: str, run_id: str, *, callee: str, actor: str | None = None) -> dict | None:
    """Persist a wrapper callee (upsert on ``(session_id, callee)``) and re-extract
    the run. Returns ``{"rule": <dict>, "recovered": <int rows written>}``, or
    ``None`` if `run_id` is invisible to `tenant_id` (RLS). ``InvalidWrapperCallee``
    propagates uncaught -> the router maps it to 422."""
    validate_callee(callee)
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)
        stmt = (
            pg_insert(models.SessionWrapper)
            .values(tenant_id=tenant_id, session_id=session_id, callee=callee, actor=actor)
            .on_conflict_do_update(
                index_elements=["session_id", "callee"],
                set_={"actor": actor, "updated_at": func.now()},
            )
            .returning(models.SessionWrapper)
        )
        row = session.scalars(stmt).one()
        rule = _as_dict(row)
        rules = queries.wrapper_rules_in_session(session, session_id)
    recovered = reextract.reextract_run(tenant_id, run_id, rules)  # own transaction(s)
    return {"rule": rule, "recovered": recovered or 0}


def list_rules(tenant_id: str, run_id: str) -> list[dict] | None:
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        rows = session.scalars(
            select(models.SessionWrapper)
            .where(models.SessionWrapper.session_id == str(run.session_id))
            .order_by(models.SessionWrapper.created_at)
        ).all()
        return [_as_dict(row) for row in rows]


def delete_rule(tenant_id: str, run_id: str, rule_id: str) -> bool | None:
    """Remove the rule so future runs / re-extracts stop recognizing the callee.
    Does NOT retract already-persisted wrapper findings (spec §8), so it does not
    re-extract."""
    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        return False
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        result = session.execute(
            delete(models.SessionWrapper).where(
                models.SessionWrapper.id == rule_uuid,
                models.SessionWrapper.session_id == str(run.session_id),
            )
        )
        return result.rowcount > 0
```

- [ ] **3.9 (GREEN-run) Run it, expect PASS.**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/wrapper_service_test.py -q -m integration
```
Expected: all five tests pass.

- [ ] **3.10 (commit)**
```
git add src/recon/findings/wrapper_service.py src/recon/findings/wrapper_service_test.py src/recon/findings/queries.py
git commit -m "feat(wrapper-teaching): add wrapper_service (add/list/delete) + session loader" -m "wrapper_service.add_rule validates the callee, upserts session_wrapper on (session_id, callee), then triggers reextract_run over the loaded session rules; delete removes the rule without re-extracting (spec §8). queries.wrapper_rules_in_session returns pure WrapperRule values, mirroring base_url_rules_in_session (spec Task 13.3, §6)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **3.11 (RED) Write the router integration tests.** Create `src/recon/api/wrappers_router_test.py`:
```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from recon import storage
from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.runs import service
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _run_with_source(redis, tenant, session_id, source: bytes) -> str:
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", source)
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))
    return view.id


def test_post_wrapper_recovers_and_lists_endpoint(client, redis, authorized_session):
    tenant, session_id = authorized_session
    run_id = _run_with_source(redis, tenant, session_id, b"const api = makeClient(); api.get('/svc');")

    resp = client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant), json={"callee": "api"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["callee"] == "api" and body["recovered"] >= 1

    # Recovered endpoint is visible in the findings read (documented/visible end-to-end).
    findings = client.get(f"/runs/{run_id}/findings", headers=_headers(tenant)).json()
    assert any(f["value"] == "GET /svc" for f in findings["findings"])


def test_get_lists_wrappers(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant), json={"callee": "api"})
    resp = client.get(f"/runs/{run_id}/wrappers", headers=_headers(tenant))
    assert resp.status_code == 200 and len(resp.json()) == 1


def test_delete_wrapper(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    rule = client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant),
                       json={"callee": "api"}).json()["rule"]
    resp = client.delete(f"/runs/{run_id}/wrappers/{rule['id']}", headers=_headers(tenant))
    assert resp.status_code == 204
    assert client.get(f"/runs/{run_id}/wrappers", headers=_headers(tenant)).json() == []


def test_invalid_callee_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    resp = client.post(f"/runs/{run_id}/wrappers", headers=_headers(tenant), json={"callee": "a.b"})
    assert resp.status_code == 422


def test_unknown_run_is_404(client, tenant):
    resp = client.post("/runs/00000000-0000-0000-0000-000000000000/wrappers",
                       headers=_headers(tenant), json={"callee": "api"})
    assert resp.status_code == 404


def test_other_tenant_run_is_404(client, redis, authorized_session):
    owner_tenant, session_id = authorized_session
    run_id = _run_with_source(redis, owner_tenant, session_id, b"const api = makeClient(); api.get('/x');")
    other = sessions_service.create_tenant("wrapper-other")
    resp = client.post(f"/runs/{run_id}/wrappers", headers=_headers(other), json={"callee": "api"})
    assert resp.status_code == 404
```

- [ ] **3.12 (RED-run) Run it, expect FAIL (route unregistered / module missing).**
```
./.venv/Scripts/python.exe -m pytest src/recon/api/wrappers_router_test.py -q -m integration
```
Expected: `404` on the POST (route not registered) or `ModuleNotFoundError: ...wrappers_router` — both RED.

- [ ] **3.13 (GREEN) Create `src/recon/api/wrappers_router.py`:**
```python
"""Taught HTTP-client wrappers for a run's session (REQ-C2 first clause, spec §6).

POST/GET/DELETE /runs/{run_id}/wrappers. Thin: validate the callee, delegate to
recon.findings.wrapper_service (which persists + re-extracts), map RLS-invisible
runs to 404, a non-identifier callee to 422, and a vanished source blob to 409.
Isolation is the database's (RLS).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from recon.api.deps import get_tenant_id
from recon.findings import reextract, wrapper_service
from recon.findings.wrappers import InvalidWrapperCallee

router = APIRouter(tags=["wrappers"])


class WrapperRuleIn(BaseModel):
    callee: str
    actor: str | None = None


@router.post("/runs/{run_id}/wrappers")
async def add_wrapper_rule(
    run_id: str, rule: WrapperRuleIn, tenant_id: str = Depends(get_tenant_id),
) -> dict:
    try:
        result = await run_in_threadpool(
            wrapper_service.add_rule, tenant_id, run_id, callee=rule.callee, actor=rule.actor,
        )
    except InvalidWrapperCallee as exc:
        raise HTTPException(status_code=422, detail=f"invalid callee: {exc}") from exc
    except reextract.SourceBlobMissing as exc:
        raise HTTPException(status_code=409, detail="run source is no longer available") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


@router.get("/runs/{run_id}/wrappers")
async def list_wrapper_rules(
    run_id: str, tenant_id: str = Depends(get_tenant_id),
) -> list[dict]:
    rules = await run_in_threadpool(wrapper_service.list_rules, tenant_id, run_id)
    if rules is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rules


@router.delete("/runs/{run_id}/wrappers/{rule_id}", status_code=204)
async def delete_wrapper_rule(
    run_id: str, rule_id: str, tenant_id: str = Depends(get_tenant_id),
) -> Response:
    deleted = await run_in_threadpool(wrapper_service.delete_rule, tenant_id, run_id, rule_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not deleted:
        raise HTTPException(status_code=404, detail="rule not found")
    return Response(status_code=204)
```

- [ ] **3.14 (GREEN) Register the router before `_mount_spa`.** In `src/recon/api/app.py`, add `wrappers_router` to the `from recon.api import (...)` tuple (keep alphabetical-ish, after `spec_router,`), and add the include after the `base_url_router` line:
```python
    app.include_router(base_url_router.router)
    app.include_router(wrappers_router.router)
```
(The SPA catch-all `_mount_spa(app, settings)` is still called last at line 52, so real API routes match first.)

- [ ] **3.15 (GREEN-run) Run the router tests, expect PASS.**
```
./.venv/Scripts/python.exe -m pytest src/recon/api/wrappers_router_test.py -q -m integration
```
Expected: all six tests pass.

- [ ] **3.16 (RED) Write the future-run analyze-recognition test.** Create `src/recon/findings/analyze_wrapper_test.py`:
```python
"""Integration test: a NEW run's analyze stage recognizes a session-scoped taught
wrapper (REQ-D5), without any explicit re-extract on that run."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze
from recon.runs import service

pytestmark = pytest.mark.integration


def _teach(tenant, session_id, callee):
    with tenant_session(tenant) as session:
        session.add(models.SessionWrapper(tenant_id=tenant, session_id=session_id, callee=callee))


def test_future_run_analyze_recognizes_taught_wrapper(redis, authorized_session):
    tenant, session_id = authorized_session
    _teach(tenant, session_id, "api")  # config exists BEFORE the run is analyzed

    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    key = storage.put_blob(tenant, view.id, "input", b"const api = makeClient(); api.get('/future');")
    with tenant_session(tenant) as session:
        session.execute(update(models.Run).where(models.Run.id == view.id).values(input_ref=key))

    analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    with tenant_session(tenant) as session:
        found = {
            f.value: f for f in session.execute(
                select(models.Finding).where(
                    models.Finding.run_id == view.id, models.Finding.type == "endpoint",
                )
            ).scalars()
        }
    assert "GET /future" in found
    assert found["GET /future"].attributes["wrapper"] == "api"
```

- [ ] **3.17 (RED-run) Run it, expect FAIL (analyze does not load wrappers yet).**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_wrapper_test.py -q -m integration
```
Expected: `KeyError: 'GET /future'` — `analyze_run` still extracts with the empty default `wrappers=()`.

- [ ] **3.18 (GREEN) Wire `analyze_run` + `_analyze_assets` to load + thread session wrappers.** In `src/recon/findings/analyze.py`, add `from recon.findings import queries` to the `from recon.findings...` import group. Replace the `analyze_run` body from `rows = run_assets.list_for_run(...)` down to `return coverage` with:
```python
    wrappers = _session_wrappers(tenant_id, run_id)  # REQ-D5: recognize taught wrappers live
    rows = run_assets.list_for_run(tenant_id, run_id)
    if rows:
        return _analyze_assets(
            redis, tenant_id=tenant_id, run_id=run_id, job_id=job_id, rows=rows, wrappers=wrappers
        )
    # ---- legacy single-blob path below (unchanged) ----
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        input_ref = run.input_ref if run is not None else None
        source_map_ref = run.source_map_ref if run is not None else None
    if not input_ref:
        return Coverage(0, 0, 0)

    with tenant_session(tenant_id) as session:  # one REQ-A3 staging transaction
        coverage, coverage_event = _analyze_blob(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            input_ref=input_ref,
            source_map_ref=source_map_ref,
            run_asset_id=None,
            asset_url=None,
            wrappers=wrappers,
        )
    publish(redis, coverage_event)
    log.info(
        "analyze.done",
        run_id=run_id,
        attributed=coverage.attributed,
        unattributed=coverage.unattributed,
        secrets=coverage.secrets,
        secrets_engine=coverage.secrets_engine,
        sources_recovered=coverage.sources_recovered,
        source_map=coverage.source_map,
        findings=coverage.findings_written,
    )
    return coverage


def _session_wrappers(tenant_id: str, run_id: str) -> list[WrapperRule]:
    """Load the taught wrapper callees for the run's session so the analyze stage
    recognizes them live on this and every future run (REQ-D5). Empty when the run
    is invisible (RLS) or has no rules."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return []
        return queries.wrapper_rules_in_session(session, str(run.session_id))
```
Then add the `wrappers` param to `_analyze_assets` (append to its keyword-only signature after `rows: list[run_assets.AssetRow],`):
```python
    wrappers: Sequence[WrapperRule] = (),
```
and inside `_analyze_assets`, add `wrappers=wrappers,` to the `_analyze_blob(...)` call (after `asset_url=asset.url,`).

- [ ] **3.19 (GREEN-run) Run the future-run test + the analyze regression suite, expect PASS.**
```
./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_wrapper_test.py src/recon/findings/analyze_test.py src/recon/findings/analyze_multi_test.py -q -m integration
```
Expected: all pass (the loader returns `[]` for runs with no rules, so the existing analyze tests are unaffected).

- [ ] **3.20 (commit)**
```
git add src/recon/api/wrappers_router.py src/recon/api/wrappers_router_test.py src/recon/api/app.py src/recon/findings/analyze.py src/recon/findings/analyze_wrapper_test.py
git commit -m "feat(wrapper-teaching): add wrappers router + wire analyze to load session wrappers" -m "POST/GET/DELETE /runs/{id}/wrappers (run_in_threadpool; 404 RLS-invisible, 422 bad callee, 409 missing blob), registered before the SPA catch-all. analyze_run/_analyze_assets now load the session's wrapper rules and thread them into _extract_endpoints so future rescans recognize a taught wrapper automatically (spec Task 13.3, §3, §11 REQ-D5/REQ-S1)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **3.21 (host-lane regression) Confirm the pure suite still passes.**
```
./.venv/Scripts/python.exe -m pytest -m "not integration" -q
```
Expected: PASS (no host-lane regression from the model/service/router additions).

---

### Task 4: FE panel

Spec §13.4, §3. A small React panel to list / add / delete a wrapper callee, mirroring `BaseUrlPanel`. Live in-container walkthrough is deferred (image rebuild), as in prior slices.

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/apiClient.ts`
- Create: `web/src/features/findings/WrapperPanel.tsx`
- Create: `web/src/features/findings/WrapperPanel.test.tsx`
- Modify: `web/src/features/findings/FindingsView.tsx`

**Interfaces:**
- Produces `WrapperRule { id: string; callee: string; actor: string | null }` and `WrapperRuleResult { rule: WrapperRule; recovered: number }` (types.ts).
- Produces `listWrapperRules(tenantId, runId): Promise<WrapperRule[]>`, `addWrapperRule(tenantId, runId, body: { callee: string; actor?: string }): Promise<WrapperRuleResult>`, `deleteWrapperRule(tenantId, runId, ruleId): Promise<void>` (apiClient.ts).
- Produces `<WrapperPanel runId={string} />`.
- Consumes the `POST/GET/DELETE /runs/{id}/wrappers` routes from Task 3.

#### Steps

- [ ] **4.1 (GREEN, types) Add the FE types.** In `web/src/api/types.ts`, append after the `BaseUrlRuleResult` interface:
```typescript
// Taught HTTP-client wrapper (design REQ-C2 first clause): a callee whose member
// calls (`api.get('/x')`) the extractor treats as endpoints. `recovered` is the
// number of finding/occurrence rows the re-extract wrote (0 when nothing is new).
export interface WrapperRule {
  id: string;
  callee: string;
  actor: string | null;
}
export interface WrapperRuleResult { rule: WrapperRule; recovered: number; }
```

- [ ] **4.2 (RED) Write the panel test.** Create `web/src/features/findings/WrapperPanel.test.tsx`:
```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WrapperPanel } from "./WrapperPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { WrapperRule } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui() {
  vi.spyOn(api, "listWrapperRules").mockResolvedValue([]);
  return render(<TenantProvider><WrapperPanel runId="r" /></TenantProvider>);
}

const EXISTING: WrapperRule = { id: "w-1", callee: "api", actor: null };

describe("WrapperPanel", () => {
  it("teaches a wrapper and lists it", async () => {
    vi.spyOn(api, "addWrapperRule").mockResolvedValue({
      rule: { id: "1", callee: "api", actor: null }, recovered: 3,
    });
    ui();
    await userEvent.type(screen.getByLabelText(/wrapper callee/i), "api");
    await userEvent.click(screen.getByRole("button", { name: /teach wrapper/i }));
    expect(api.addWrapperRule).toHaveBeenCalledWith(TENANT, "r", { callee: "api" });
    expect(await screen.findByText(/recovered 3 rows/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete wrapper api" })).toBeInTheDocument();
  });

  it("shows a readable message on a 422 invalid callee", async () => {
    vi.spyOn(api, "addWrapperRule").mockRejectedValue(new api.ApiError(422, "invalid callee: 'a.b'"));
    ui();
    await userEvent.type(screen.getByLabelText(/wrapper callee/i), "a.b");
    await userEvent.click(screen.getByRole("button", { name: /teach wrapper/i }));
    expect(await screen.findByText(/invalid callee/i)).toBeInTheDocument();
  });

  it("deletes a wrapper and removes it from the list", async () => {
    vi.spyOn(api, "listWrapperRules").mockResolvedValue([EXISTING]);
    vi.spyOn(api, "deleteWrapperRule").mockResolvedValue(undefined);
    render(<TenantProvider><WrapperPanel runId="r" /></TenantProvider>);
    const del = await screen.findByRole("button", { name: "Delete wrapper api" });
    await userEvent.click(del);
    expect(api.deleteWrapperRule).toHaveBeenCalledWith(TENANT, "r", "w-1");
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Delete wrapper api" })).not.toBeInTheDocument();
    });
  });
});
```

- [ ] **4.3 (RED-run) Run it, expect FAIL.**
```
cd web && npx vitest run src/features/findings/WrapperPanel.test.tsx
```
Expected: fails to resolve `./WrapperPanel` and `addWrapperRule`/`listWrapperRules`/`deleteWrapperRule` (not exported yet).

- [ ] **4.4 (GREEN, client) Add the API client functions.** In `web/src/api/apiClient.ts`, add `WrapperRule, WrapperRuleResult` to the `import type { ... } from "./types";` list, and append after `deleteBaseUrlRule`:
```typescript
export function listWrapperRules(tenantId: string, runId: string): Promise<WrapperRule[]> {
  return request(`/runs/${encodeURIComponent(runId)}/wrappers`, {}, tenantId);
}

export function addWrapperRule(
  tenantId: string, runId: string, body: { callee: string; actor?: string },
): Promise<WrapperRuleResult> {
  return request(`/runs/${encodeURIComponent(runId)}/wrappers`, json("POST", body), tenantId);
}

export function deleteWrapperRule(tenantId: string, runId: string, ruleId: string): Promise<void> {
  return request(
    `/runs/${encodeURIComponent(runId)}/wrappers/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" }, tenantId,
  );
}
```

- [ ] **4.5 (GREEN, component) Create `web/src/features/findings/WrapperPanel.tsx`:**
```tsx
import { useEffect, useState } from "react";
import type React from "react";
import { useTenant } from "../../tenant/TenantContext";
import { addWrapperRule, deleteWrapperRule, listWrapperRules, ApiError } from "../../api/apiClient";
import type { WrapperRule } from "../../api/types";

export function WrapperPanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const [rules, setRules] = useState<WrapperRule[]>([]);
  const [callee, setCallee] = useState("");
  const [recovered, setRecovered] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    listWrapperRules(tenantId, runId).then(setRules).catch(() => { /* first load best-effort */ });
  }, [tenantId, runId]);

  const ready = callee.trim() !== "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      const res = await addWrapperRule(tenantId, runId, { callee: callee.trim() });
      setRules((prev) => [...prev.filter((r) => r.callee !== res.rule.callee), res.rule]);
      setRecovered(res.recovered);
      setCallee("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add wrapper");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!tenantId || busy) return;
    setBusy(true); setError(null);
    try {
      await deleteWrapperRule(tenantId, runId, id);
      setRules((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete wrapper");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>HTTP-client wrapper</h3>
      <p className="muted">Teach the extractor a custom client so <code>api.get('/x')</code>-style calls become endpoints.</p>
      <div>
        <label htmlFor="wrapper-callee">Wrapper callee</label>
        <input id="wrapper-callee" value={callee} onChange={(e) => setCallee(e.target.value)}
          placeholder="api" />
      </div>
      {error && <p className="sev-high">{error}</p>}
      <button type="submit" disabled={!ready || busy}>{busy ? "Teaching…" : "Teach wrapper"}</button>
      {recovered !== null && <p className="muted">recovered {recovered} rows</p>}
      <ul>
        {rules.map((r) => (
          <li key={r.id}>
            <code>{r.callee}</code>
            <button type="button" onClick={() => remove(r.id)} aria-label={`Delete wrapper ${r.callee}`}>Delete</button>
          </li>
        ))}
      </ul>
    </form>
  );
}
```

- [ ] **4.6 (GREEN-run) Run the panel test, expect PASS.**
```
cd web && npx vitest run src/features/findings/WrapperPanel.test.tsx
```
Expected: all three tests pass.

- [ ] **4.7 (GREEN, wire-in) Render the panel in `FindingsView`.** In `web/src/features/findings/FindingsView.tsx`, add the import after `import { BaseUrlPanel } from "./BaseUrlPanel";`:
```tsx
import { WrapperPanel } from "./WrapperPanel";
```
and render it right after the `<BaseUrlPanel runId={runId} />` line:
```tsx
      <BaseUrlPanel runId={runId} />
      <WrapperPanel runId={runId} />
```

- [ ] **4.8 (GREEN-run) Typecheck + full FE suite, expect PASS.**
```
cd web && npx tsc -b --noEmit && npx vitest run
```
Expected: clean typecheck; all FE tests pass.

- [ ] **4.9 (commit)**
```
git add web/src/api/types.ts web/src/api/apiClient.ts web/src/features/findings/WrapperPanel.tsx web/src/features/findings/WrapperPanel.test.tsx web/src/features/findings/FindingsView.tsx
git commit -m "feat(wrapper-teaching): add WrapperPanel to teach a custom client callee" -m "New WrapperPanel (list/add/delete a wrapper callee) wired into FindingsView beside BaseUrlPanel, with apiClient fns + types for POST/GET/DELETE /runs/{id}/wrappers and colocated Vitest. Live in-container walkthrough deferred to an image rebuild, as in prior slices (spec Task 13.4, §3)." -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **4.10 (deferred, note-only) Record the deferred live walkthrough.** Do NOT rebuild the container image here. Note in the branch/handoff that the in-container visual walkthrough (Teach wrapper -> recovered endpoint appears) is owed once the SPA image is rebuilt, mirroring the base-URL / ui-catch-up slices.

---

## Self-review (writing-plans)

**Spec-coverage — every spec section/requirement maps to a task:**

| Spec | Mapped to |
|---|---|
| §2.1 first-class endpoint findings | Task 1 (kind="axios", stable identity) + Task 2 (`_record_endpoint`) |
| §2.2 persisted re-extraction (approach P) | Task 2 (`reextract.py`) |
| §2.3 name-based member matcher + `.request` | Task 1 (steps 1.6-1.15) |
| §2.4 / §8 config-removal = documented limit | Task 3 `delete_rule` (no re-extract), step 3.8 docstring |
| §2.5 session config, run-scoped re-extract | Task 3 model (session) + Task 2 `reextract_run` (run-scoped) |
| §2.6 / §12 Blocker 1 coverage not re-emitted | Task 2 `_extract_endpoints` (no event) + test 2.8 multi-asset |
| §2.7 / §12 Blocker 2 in-request threadpool, no subprocess | Task 3 router `run_in_threadpool` + Task 2 helper (no kingfisher) |
| §3 components (all NEW/EDIT units) | Tasks 1-4 Files blocks |
| §4 matcher + dispatch-last collision safety | Task 1 (step 1.12 + collision tests) |
| §5 data model | Task 3 (step 3.1) |
| §6 re-extract mechanics | Task 2 |
| §7 provenance (`wrapper` attr, kind stays axios) | Task 1 (`RawEndpoint.wrapper`) + Task 2 (`_record_endpoint`) |
| §11 REQ-C2/D3/A3/D5/S1/A1/P1-P2 | REQ-D3 Task 2 hash-stability test; REQ-A3 idempotency test; REQ-D5 Task 3 steps 3.16-3.20; REQ-S1 migration RLS + 404 tests; REQ-A1 threadpool; REQ-P Task 2 static-only |
| §12 Imp 3 (kind gate) / Imp 4 (source-map path) / Minor 6 (`.request`) / Minor 7 (dispatch order) / Minor 8 (WRAPPER_TABLES) / Minor 9 (missing blob) | Tasks 1-3 as cited inline |

**Placeholder scan:** no "TBD"/"similar to Task N"/"add error handling"/"write tests for the above" — every code + test step shows real code, repeated rather than cross-referenced.

**Type-consistency across tasks (verified):** `WrapperRule(callee: str)` (Task 1) is the single value used by `extract`, `_extract_endpoints`, `reextract_run`, `queries.wrapper_rules_in_session`, and `analyze._session_wrappers`. `reextract_run(...) -> int | None` feeds `add_rule`'s `recovered` (int, `None`→0), surfaced as FE `WrapperRuleResult.recovered: number`. `add_rule` returns `{"rule": {"id","callee","actor"}, "recovered": int}` matching FE `WrapperRule` + `WrapperRuleResult`. `RawEndpoint.wrapper: str | None` → `attributes["wrapper"]` → asserted in Task 2/3 integration tests. No signature drift found.
