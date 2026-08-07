# D3 · mypy incremental typing — design (for §4 adversarial gate)

**Repo:** `js-recon-v2` @ `main = a0deddc`. Backend: `apps/platform`, src layout `src/recon/*`.

## Goal
Introduce mypy incrementally per DEBT.md D3: `--strict` on **`recon.findings` + `recon.spec`
only**, fix every resulting error, gate it in CI's `host-tests` lane. `db/models.py` (194
errors) and all other packages stay OUT of scope this slice (widened later, module-by-module).

## Measured surface (ground truth, post-D2 `main`)
```
uv run --with mypy mypy --strict --ignore-missing-imports --follow-imports=silent \
  --exclude '_test\.py$' src/recon/findings src/recon/spec
→ Found 37 errors in 9 files (checked 18 source files)
```
(The 18 = the non-test `.py` in the two packages; tests correctly excluded.)

### Categorized (all 37)
| # | Bucket | Errors | Fix | Runtime change? |
|---|--------|--------|-----|-----------------|
| A | Pure annotation `no-untyped-def` ×11 + `type-arg` bare `dict` ×14 | 25 | add annotations / `dict[K,V]` | none |
| B | SQLAlchemy DML `Result` / Any-leak | 5 | `Result` `.rowcount` typing + `re.findall`→`str` annotate | none |
| C | analyze.py:605 `offset_start=offset_end=None` inferred `int` | 1 | type `int \| None` (offset-less occurrence is a real, intended state → reveal 422) | none (type only) |
| D | ingest.py:71 subclass of `yaml.SafeLoader` (=Any under strict) | 1 | add `types-PyYAML` dev dep (also types `compose_node` at :86, one of the ×11) | none |
| E | queries.py:199 `Sequence[Finding]` vs `list[Finding]` param | 1 | widen `_run_spec_summary` param to `Sequence[Finding]` (iterate-only) | none |
| F | extract.py:473 `prop: str` rebound `str\|None` | 1 | introduce local `index`, `None`-return, then `prop = index` (type-narrow) | none |
| **G** | **Real None-safety (3) — the judgment calls** | 3 | see below | none (all invariant/neutral) |

### G — the three None-safety sites (assert-vs-guard, evidence-backed)
1. **extract.py:106** — `_text(node: Node|None) -> str`: `node.text.decode(...)`; tree-sitter's
   `Node.text` is `bytes|None`. Invariant: `.text` is present when the tree is parsed from
   source bytes (always, here). Fix: **`(node.text or b"").decode("utf-8","replace")`** — matches
   the function's own empty-on-absence contract (it already returns `""` when `node is None`). Not
   a real-bug guard; a stub-Optional handled with the fn's existing default.
2. **analyze.py:222** — `asset.input_ref` (`str|None`, models.py:388) → `_analyze_blob(input_ref: str)`.
   **Invariant:** `runs/assets.set_fetch_ok(session, asset_id, input_ref: str)` (assets.py:77) writes
   `input_ref` + `fetch_status=OK` *atomically*; the only other writer `set_fetch_failed` sets FAILED;
   the analyze loop (analyze.py:207) `continue`s unless `fetch_status==OK`. So
   `fetch_status==OK ⟹ input_ref is not None`. Fix: **`assert asset.input_ref is not None`** + comment
   → `set_fetch_ok`. (assert, because None is unreachable by invariant — not a guard.)
3. **queries.py:347** — `Classification(row.status, row.reason, row.matched_operation)`:
   `Classification.reason: str` (classify.py:133) vs `FindingSpecStatus.reason: Mapped[str|None]`
   (models.py:495). Write path (spec/service.py:239) always writes `reason=classification.reason` (a
   `str`) ⇒ never persisted None. Read path feeds `summarize()` → `if c.reason == "suffix-verify"`
   (classify.py:284). Fix: **`row.reason or ""`** — value-neutral (None can never equal
   "suffix-verify", so bucketing is identical) and crash-free on a read path (vs `assert`).

## Config (`[tool.mypy]` in pyproject.toml)
- Base: `mypy_path="src"`, `explicit_package_bases=true`, `ignore_missing_imports=true`,
  `follow_imports="silent"`, `exclude=['_test\.py$']`, `warn_unused_configs=true`.
- Per-module override `module=["recon.findings.*","recon.spec.*"]` enabling the strict sub-flags
  (mypy has no per-module `strict` meta-flag — spell them out: `disallow_untyped_defs`,
  `disallow_incomplete_defs`, `disallow_untyped_calls`, `disallow_any_generics`,
  `disallow_untyped_decorators`, `disallow_subclassing_any`, `check_untyped_defs`,
  `warn_return_any`, `warn_redundant_casts`, `warn_unused_ignores`, `strict_equality`, `extra_checks`).
- Dev deps: add `mypy`, `types-PyYAML`; regen `uv.lock` (`uv lock`).
- CI `host-tests` lane: add a `uv run mypy src/recon/findings src/recon/spec` step (fail = red).
- The pyproject config must reproduce exactly the 37 (validated before fixing = red baseline).

## TDD
1. Add config + dev deps → `mypy` shows 37 (red baseline).
2. Fix in isolated buckets → `mypy` → 0 (green); fast-lane `pytest -m "not integration"` stays green.
3. New runtime tests: the 3 G-sites are invariant-unreachable (extract:106, analyze:222) or
   value-neutral (queries:347), so a new None-path test would assert a branch that cannot fire /
   cannot differ — **no new behavioral test proposed.** ← adversarial question for you.

## Questions for the adversarial review
- Any error mis-categorized (esp. a "mechanical" one that actually needs a runtime decision)?
- G-sites: is any None path actually **reachable & observable** such that `assert`/`or ""` hides a
  real bug and a guard+test is required instead? Disprove with exact code lines.
- Config: does the per-module-override-strict correctly reproduce all 37 (vs CLI `--strict`)? Any
  scope leak (e.g. accidentally checking `db/models.py` via `follow_imports`)?
- CI: any reason the mypy step is flaky/non-hermetic (it needs no engines/infra)?

Verdict required: **SHIP AS-IS / BUILD WITH CHANGES (enumerated) / REJECT (why)** — evidence-backed
(official mypy docs or exact repo lines), no rubber-stamp.

---

## §4 design gate OUTCOME (2026-08-07) — reconciled, authoritative

Two adversarial engineer subagents reviewed independently:
- **Meta IC8 (correctness): SHIP AS-IS.** All 3 G-site None-safety calls verified correct with exact
  lines. `assert` at analyze.py:222 confirmed safe: it's inside the `try` (analyze.py:216-234), so a
  (unreachable) `AssertionError` lands at `except Exception` → `set_analyze_failed` = logged per-asset
  skip, not a crashed job. Existing `capture_router_test.py:126` already pins the OK⟹input_ref invariant.
  Buckets C (analyze:605 — `Occurrence.offset_start/end` are already `int|None` in store.py:32-33) and
  F (extract:473) confirmed type-only. Concurs: no new behavioral test warranted.
- **Google staff (config/tooling): BUILD WITH CHANGES.** Verified the pyproject config reproduces the
  37 exactly, no `db/models.py` scope leak (follow_imports=silent), tests excluded, CI hermetic,
  reproducible under `uv sync --frozen`. Two changes below.

**RECONCILED DELTAS (apply in build):**
1. **Drop `types-PyYAML`.** It removes ingest.py:71 but *introduces* `ingest.py:87
   no-untyped-call` on the stub's unannotated `peek_event` (persists even after annotating
   `compose_node`). Simpler + no new dep/lock churn: **`# type: ignore[misc]`** on the
   `class _NoAliasSafeLoader(yaml.SafeLoader):` line, keep the `compose_node` annotation (already in
   bucket A). Dev extra gains **`mypy` only**. (`warn_unused_ignores` confirms the ignore is needed.)
2. **Use per-module `strict = true`** in the override (not the enumerated 12 flags, which omit
   `no_implicit_reexport` and so weren't true `--strict`). Verified: per-module `strict = true` is
   accepted by the pinned mypy and reproduces the 37 with no warning. Simplest + honestly `--strict`;
   mypy is lock-pinned so it can only expand at a deliberate `uv lock`.

Everything else ships as specified. **Both gates passed** (Meta SHIP; Google BUILD-WITH-CHANGES, both
deltas are simplifications). Proceed to TDD build; red baseline must still be exactly 37.
